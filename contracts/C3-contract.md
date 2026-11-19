# C3 Contract: LangGraph Triage (+ 2 additive C1 changes)

Owner: FORGE Architecture. Next: FORGE Implementation, then FORGE QA. Base: `main` at 1af6317. Sources: brief v0.4 §5 (5.1–5.3), §4, §10, and contracts C1 and C2.
Stack: Python 3.11, `langgraph`, `httpx`, `pyyaml` and Pydantic v2. There is no Docker. CI makes no network calls and uses recorded fixtures.

## 0. Design decisions
1. **LangGraph orchestrates, and the Case API decides state** (brief §5.2). The triage service talks to Mock Splunk and the Case API over HTTP only, never by importing their stores. Grok only does inference.
2. **Routing is policy, not model output.** The model's `recommended_route` is recorded but advisory. `config/routing.yaml` combined with the pure `routing.py` decides the route. Severity and confidence are separate inputs, and no confidence threshold ships enabled (brief §5.3 says thresholds need approval).
3. **No guessed results.** A failed call or output that fails validation goes to `NEEDS_REVIEW` with the reason recorded. The severity stays untouched; nothing defaults to Splunk's label or to "medium".
4. **Provenance goes in as evidence, so C1 needs no TriageCreate change.** The full validated assessment is attached as evidence with `source="triage_assessment"` and `ref=<response id>`. That evidence carries the model name the API *returned* (alongside the requested one), the response id, the `x-request-id` header if present, usage, MITRE ids, `triggered_at` and the route. `TriageCreate.model` is set to the **returned** model (e.g. `grok-4.3` even when `grok-3-mini` was requested).
5. **Ingest is idempotent** (ARCHON's ruling). A 409 duplicate `alert_id` triggers a lookup by `alert_id` and a log line. If the case is past `NEW`, triage stops without re-triaging.

## 1. Required additive C1 changes (backward compatible: nothing removed or renamed)
C1 has a store method `get_case_by_alert_id` but no API route for it, and no review state. This change adds exactly:
1. **`GET /v1/cases?alert_id=<str>`**, an optional exact-match filter that returns 0 or 1 items. Wire it through `routes.list_cases`, `CaseService.list_cases` and `Store.list_cases`.
2. **Status `NEEDS_REVIEW`**, appended at the end of `CaseStatus`, plus one new operation:
   - `POST /v1/cases/{case_id}/review-flag` with body `ReviewFlagCreate {reason: Literal["model_call_failed","invalid_model_output","no_route_matched"], detail: str(1..2000), actor}`. It returns 200 `Case`, with errors 404, 409 and 422 (`_errors(404, 409, 422)`).
   - Audit event `case.review_flagged`, with payload `{reason, detail}`.

Transition table additions in `state.py` (existing rows are unchanged):

| Operation | Allowed from | To |
|---|---|---|
| flag_review (new) | NEW, TRIAGED | NEEDS_REVIEW |
| triage (existing op, new source state) | NEEDS_REVIEW | TRIAGED |
| add_evidence (existing op, new source state) | NEEDS_REVIEW | (unchanged) |

Leaving review takes a human or a retry calling `/triage`. Every other operation from `NEEDS_REVIEW` returns 409. Regenerate `contracts/openapi.json`, and extend `DOCUMENTED` and `STATUSES` in `test_state.py` with these rows.

## 2. Graph (`services/triage/graph.py`, a `StateGraph[TriageState]`)
`TriageState` is a TypedDict holding `alert_id`, `alert`, `case`, `skipped_reason`, `splunk_evidence`, `evidence_map` (evidence_id to case evidence id), `facts`, `grok_raw`, `assessment`, `route`, `rule`, `error_reason`, `error_detail` and `outcome`.

| Node | Does | Next |
|---|---|---|
| `fetch_alert` | Mock Splunk `GET /v1/alerts/{id}` | `ingest_case`; a 404 raises `AlertNotFound`, with no case created |
| `ingest_case` | `POST /v1/cases` (via `alert_to_case_create`). On 409 it calls `GET /v1/cases?alert_id=` and logs `ingest.duplicate` | status `NEW` goes to `fetch_evidence` (resuming a crashed run is not a re-triage); any other status goes to END with `outcome="skipped_existing"` |
| `fetch_evidence` | `GET /v1/alerts/{id}/evidence` | `attach_evidence` |
| `attach_evidence` | Reads the case's evidence, then POSTs only the refs not already attached (via `evidence_to_evidence_create`) | `deterministic_checks` |
| `deterministic_checks` | Pure `checks.py`: computes `privileged_account`, `first_seen_country`, `rdp_lateral_hosts`, `evidence_count` and the alert's `mitre_techniques`. These are **facts only, with no severity** | `grok_assess` |
| `grok_assess` | Builds the prompt (`prompt.py`), calls `GrokClient` and stores the raw response | `validate`; transport failure after 1 retry goes to `flag_review(model_call_failed)` |
| `validate` | Parses `GrokAssessment` and checks that `evidence_references` ⊆ the supplied evidence_ids | `route`; any failure goes to `flag_review(invalid_model_output)` |
| `route` | `routing.decide(facts, assessment, splunk_severity, config)` returns `(route, rule)` | `persist_triage`; `default_route: review` goes to `persist_triage` then `flag_review(no_route_matched)` |
| `persist_triage` | Attaches the `triage_assessment` evidence (§3.3), then `POST /triage` | END `outcome="triaged"`, or `flag_review` when the route is review |
| `flag_review` | Attaches `triage_failure` evidence `{reason, detail, model_requested, model_returned?, raw_excerpt ≤ 2000 chars}`, then `POST /review-flag` | END `outcome="needs_review"` |

The triage actor is `{"id": "triage-langgraph", "type": "system"}`. Entry point: `python -m services.triage run --alert-id SPL-2026-0001` (env `CASE_API_URL`, `MOCK_SPLUNK_URL`). It prints a JSON `TriageResult {alert_id, case_id, outcome, route, rule, assessed_severity?, model_requested, model_returned?, response_id?}`.

## 3. Grok contract
### 3.1 Request (`services/triage/grok.py`, `HttpGrokClient`)
- `POST {XAI_BASE_URL:-https://api.x.ai/v1}/chat/completions`, header `Authorization: Bearer $XAI_API_KEY`, timeout 30 s, 1 retry on timeout or 5xx only.
- Body: `{"model": $XAI_MODEL (default "grok-3-mini"), "temperature": 0, "messages": [{"role":"system","content": SYSTEM_PROMPT}, {"role":"user","content": <json: alert, facts, evidence[{evidence_id, source, timestamp, content}]>}], "response_format": {"type":"json_schema","json_schema":{"name":"triage_assessment","strict":true,"schema": GrokAssessment.model_json_schema()}}}`
- A missing `XAI_API_KEY` raises `ConfigError` before any network call. The key is never logged or persisted.

### 3.2 Response handling
The client reads `id`, `model`, `choices[0].message.content` and `usage`, plus the `x-request-id` response header if present (otherwise null; never made up). The content is parsed as JSON into:

`GrokAssessment` (in `contracts/triage.py`, `extra="forbid"`):
```
original_severity: Severity                # echo of Splunk's label
assessed_severity: Severity
assessment_confidence: float [0,1]         # model-expressed, not calibrated (brief §5.1)
rationale: str (1..4000)
evidence_references: list[str] (≥1, each must be a supplied evidence_id)
mitre_techniques: list[str] (pattern ^T\d{4}(\.\d{3})?$)
recommended_route: Literal["collaborative","autonomous","review"]   # advisory only
additional_evidence_needed: list[str]
```
### 3.3 What gets persisted on success
- Evidence `source="triage_assessment"`, `ref=<response id>`, with content `{model_requested, model_returned, response_id, request_id, usage, assessment: <GrokAssessment>, facts, route, rule, alert_triggered_at, alert_mitre_techniques}`.
- `POST /triage` with `{severity: assessed_severity, rationale: <rationale> + "\n\nRoute: <route> (rule <rule>). MITRE: <ids>. Triggered: <triggered_at>.", model: model_returned}`.

### 3.4 Fixtures (`tests/triage/fixtures/`)
- `grok_seed_ok.json`: a full chat-completions response for SPL-2026-0001, with `"model": "grok-4.3"`, `id` and `usage`. It starts as a **hand-written file in the xAI response shape**, labeled with a top-level `"_fixture_note"` that the loader strips. It is replaced with a real recording via `make triage-record`. Tests assert structure, not wording.
- `grok_not_json.json`, `grok_bad_severity.json`, `grok_unknown_evidence_ref.json` and `grok_missing_field.json` cover invalid output.
- Tests use `FixtureGrokClient(path)`. A conftest autouse guard makes `HttpGrokClient` raise if it's constructed without the test marker `live`.

## 4. Routing
`config/routing.yaml`, validated by `RoutingConfig` in `routing.py`. Rules are checked in order, the first match wins, and multiple keys in one `when` are ANDed:
```yaml
version: 1
privileged_account_patterns: ['^adm\.', '^da-', '^svc-admin']
rules:
  - {name: privileged_account_collaborative, when: {privileged_account: true}, route: collaborative}
  - {name: high_or_critical_collaborative, when: {assessed_severity_in: [high, critical]}, route: collaborative}
  - {name: low_medium_autonomous, when: {assessed_severity_in: [low, medium]}, route: autonomous}
default_route: review
```
- Allowed `when` keys: `privileged_account: bool`, `assessed_severity_in: [Severity]`, `splunk_severity_in: [Severity]` and `confidence_below: float`. The last one is supported but **unused**, because thresholds need approval. An unknown key fails config load.
- `route` is one of `collaborative` (CENTURION's human-led queue), `autonomous` (LEGION) or `review`.
- "Always treat privileged accounts as collaborative" is the single first line under `rules`, and `test_privileged_account_always_collaborative` pins it.
- In C1 terms, collaborative and autonomous both end in status `TRIAGED`, and the route is readable from the `triage_assessment` evidence and the rationale trailer. Review ends in `NEEDS_REVIEW`. Initial triage never closes a case (brief §5.3).

## 5. Prompt (`services/triage/prompt.py`)
`SYSTEM_PROMPT` is a constant, and `build_user_message(alert, facts, evidence) -> str` is a pure function that emits deterministic JSON with sorted keys. The instructions say to: assess security severity using only the supplied evidence; cite evidence_ids; keep severity confidence separate from Splunk's label; flag missing evidence; and return only JSON that matches the schema. There are no scenario-specific hints and no expected answer (brief §9, Scenario C).

## 6. File layout (new, or changed with ~)
```
contracts/triage.py                  # GrokAssessment, TriageResult, ReviewFlagCreate re-export
~contracts/models.py                 # + NEEDS_REVIEW, ReviewFlagCreate
~contracts/openapi.json              # regenerated
contracts/C3-contract.md
config/routing.yaml
services/triage/__init__.py
services/triage/__main__.py          # CLI: run --alert-id
services/triage/config.py            # env: XAI_API_KEY, XAI_MODEL, XAI_BASE_URL, CASE_API_URL, MOCK_SPLUNK_URL, ROUTING_CONFIG
services/triage/clients.py           # CaseApiClient, SplunkClient (httpx; transport injectable)
services/triage/grok.py              # GrokClient Protocol, HttpGrokClient, FixtureGrokClient, GrokResponse
services/triage/checks.py            # pure deterministic facts
services/triage/prompt.py
services/triage/routing.py           # RoutingConfig, load_routing(), decide() (pure)
services/triage/graph.py             # nodes + build_graph(deps)
~services/case_api/{state,service,routes,store}.py   # §1
scripts/triage_smoke.py              # live run; --record writes tests/triage/fixtures/grok_seed_ok.json
Makefile                             # test, lint, triage-smoke, triage-record
~pyproject.toml                      # + langgraph, pyyaml; dev + types-PyYAML; pytest marker "live"; mypy override ignore_missing_imports for langgraph only if needed
~.cursor/rules/architecture.mdc      # + routing lives only in config/routing.yaml + routing.py; triage talks to services over HTTP
~README.md                           # triage run + smoke instructions
tests/triage/{__init__,conftest}.py  # in-process apps via httpx ASGITransport, tmp DB, Mock Splunk reset
tests/triage/test_checks.py
tests/triage/test_prompt.py
tests/triage/test_grok.py
tests/triage/test_routing.py
tests/triage/test_graph.py
~tests/case_api/{test_state,test_api,test_audit}.py  # §1
```
**Live smoke** (on Vihag's laptop): `make triage-smoke` runs `python scripts/triage_smoke.py`. It starts both apps in-process on a temp DB, resets Mock Splunk, makes one real Grok call for SPL-2026-0001 and prints the `TriageResult`, the requested and returned model, and the response id. It exits 2 with a clear message when `XAI_API_KEY` is unset. CI never runs it.

## 7. Named pytest cases by requirement
**Graph happy path (fixture Grok, in-process apps)**
- `test_graph.py::test_seed_alert_triaged_end_to_end` (case TRIAGED, 7 Splunk evidence plus 1 triage_assessment)
- `test_graph.py::test_audit_order_ingested_evidence_assessment_triaged` (case.ingested, 7× evidence.added, evidence.added[triage_assessment], case.triaged)
- `test_graph.py::test_persists_returned_model_not_requested` (requested grok-3-mini, returned grok-4.3; triage_model and evidence are both grok-4.3, and model_requested is grok-3-mini)
- `test_graph.py::test_persists_response_id_and_request_id`
- `test_graph.py::test_mitre_and_triggered_at_in_evidence_and_rationale`

**Idempotent ingest**
- `test_graph.py::test_second_run_skips_existing_case_without_retriage` (one case.triaged event total, outcome skipped_existing, Grok called once)
- `test_graph.py::test_duplicate_logs_ingest_duplicate`
- `test_graph.py::test_resume_from_new_attaches_only_missing_evidence`
- `test_api.py::test_list_cases_filter_by_alert_id` (C1)

**Review state, never a guess**
- `test_graph.py::test_model_call_failure_flags_review` (HTTP 500 twice)
- `test_graph.py::test_missing_api_key_fails_before_network`
- `test_graph.py::test_invalid_output_flags_review[not_json|bad_severity|unknown_evidence_ref|missing_field]` (status NEEDS_REVIEW, triage_severity null, triage_failure evidence present)
- `test_state.py::test_transition_table_allows_only_documented_moves` (extended with §1)
- `test_api.py::test_review_flag_from_investigating_returns_409`
- `test_api.py::test_retriage_from_needs_review_returns_triaged`
- `test_audit.py::test_review_flag_writes_one_case_review_flagged_event`

**Grok request and validation**
- `test_grok.py::test_request_body_has_model_schema_and_temperature_0`
- `test_grok.py::test_api_key_only_from_env_and_not_logged` (caplog)
- `test_grok.py::test_retry_once_on_5xx_not_on_4xx`
- `test_grok.py::test_assessment_rejects_confidence_out_of_range`

**Deterministic checks and prompt**
- `test_checks.py::test_seed_facts` (privileged true, first_seen_country true, rdp_lateral_hosts ["JMP-01","FIN-DB-01"])
- `test_checks.py::test_checks_do_not_set_severity`
- `test_prompt.py::test_user_message_is_deterministic_and_lists_evidence_ids`
- `test_prompt.py::test_prompt_contains_no_expected_answer` (no "high" or "critical" verdict strings in SYSTEM_PROMPT)

**Routing**
- `test_routing.py::test_privileged_account_always_collaborative` (even when assessed low)
- `test_routing.py::test_high_critical_collaborative`
- `test_routing.py::test_low_medium_autonomous`
- `test_routing.py::test_first_match_wins_and_default_review`
- `test_routing.py::test_unknown_when_key_fails_load`
- `test_routing.py::test_model_recommended_route_is_advisory`

**Contracts and CI**
- `test_openapi_drift.py::test_committed_openapi_matches_app[case_api]` (regenerated)
- `test_graph.py::test_no_network_in_ci` (the autouse guard)

Done means: CI is green (ruff, mypy, pytest, OpenAPI check with no network) and every test above passes. `make triage-smoke` runs on the laptop with a real key and prints the returned model and response id.

## 8. Out of scope, limits and budget
- Out of scope: LEGION investigation, Mock SOAR, routines and confidence thresholds (pending approval), and batch or polling ingest of the whole feed (C3 triages one alert id per run).
- Limit: the hand-written `grok_seed_ok.json` is a test fixture only. Demo claims must come from `make triage-smoke`, or from a re-recorded fixture with `_fixture_note` removed.
- Budget: about 12 new files plus small C1 edits. If the run is tight, cut `triage-record`, `--record` and `test_resume_from_new_attaches_only_missing_evidence` first.
