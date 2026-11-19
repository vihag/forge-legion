# C2 Contract: Mock Splunk (/v1) + C1 follow-ups

Owner: FORGE Architecture. Next: FORGE Implementation, then FORGE QA. Base: `main` at 8bd18c9 (C1 merged). Sources: brief v0.4 §9, §4, §10, and plan section C (seed).
Stack: the same as C1 (Python 3.11, FastAPI, Pydantic v2), with no database and no Docker. State is in memory and loaded from JSON fixtures.

## 0. Design decisions
1. **Mock Splunk is a read-only data source plus two control endpoints** (`reset`, `replay`). It holds no case logic and writes nothing to the Case API. Ingestion into the Case API is C3's job (LangGraph), using the mapping in §5.
2. **Everything is deterministic.** Data comes only from fixtures, and there is no randomness. Lists sort by `(timestamp, evidence_id)` or `seq`. The only clock-dependent field is `emitted_at` on replays, and the clock is injected (`create_app(clock=...)`), so tests use a fixed clock.
3. **Sources are plug-ins.** Each source is one module plus one fixture file plus one registry line (§4). The API types `content` as `dict[str, Any]`, and `source` is a string checked against the registry at runtime, not an enum. Adding a source therefore **does not change the OpenAPI spec**.
4. **The spec is a separate file.** `contracts/mock_splunk.openapi.json` is generated and drift-checked by the same `scripts/export_openapi.py` command C1 uses. The C1 file `contracts/openapi.json` keeps its name, so nothing downstream breaks.
5. **Fields line up with C1.** `Alert` uses C1's names for the shared fields (`alert_id`, `title`, `splunk_severity`, `summary`). `Evidence.evidence_id` becomes C1 `EvidenceCreate.ref`. The pure functions in `contracts/ingest_mapping.py` do the mapping, so C3 doesn't guess.

## 1. Endpoints (Mock Splunk, default port 8001)
Errors reuse C1's `ErrorResponse {error, message, details}`.

| # | Purpose | Method & path | Request | Response | Errors |
|---|---|---|---|---|---|
| 1 | health | GET `/healthz` | – | `{status:"ok"}` | – |
| 2 | list alerts (feed) | GET `/v1/alerts?after_seq=0&severity=&limit=100` | query | `AlertPage {items: Alert[], next_after_seq}` | 422 |
| 3 | get alert | GET `/v1/alerts/{alert_id}` | – | `Alert` | 404 `alert_not_found` |
| 4 | evidence by alert | GET `/v1/alerts/{alert_id}/evidence?source=` | query (source optional) | `EvidenceList {items: SplunkEvidence[]}` | 404, 422 `unknown_source` |
| 5 | evidence by window | GET `/v1/evidence?source=&start=&end=&entity=` | `source`, `start` and `end` required; `entity` optional | `EvidenceList` | 422 (`unknown_source`, `invalid_window` when start ≥ end, window > 7 days, or the time is naive) |
| 6 | list sources | GET `/v1/sources` | – | `SourceList {items: [{name, id_prefix, record_count}]}` | – |
| 7 | reset | POST `/v1/reset` | `{}` | `ResetResponse {seed_version, alerts, evidence}` | – |
| 8 | replay | POST `/v1/replay` | `ReplayRequest {alert_id}` | 201 `Alert` (new emission) | 404, 422 |

**Window semantics:** `start <= timestamp < end`, with ISO-8601 times that must carry a zone. `entity` matches any value in a record's `entities`, case-insensitively. This includes evidence not linked to any alert, such as the baseline login in §3.
**Replay semantics:** a replay appends a new emission with the next `seq`, `alert_id = "<root>-R<n>"` (n = 1, 2, … per root), `replay_of = <root>` and `emitted_at = clock()`. Evidence for a replayed alert resolves through `replay_of` to the root's evidence, with the same `evidence_id`s. Replaying a replay resolves to the root. Each emission has a distinct `alert_id`, so C1's 409 on a duplicate `alert_id` never fires on a replay.
**Reset semantics:** reset reloads the fixtures, drops all replays and returns `seq` to its seeded values. It is idempotent.

## 2. Models (`contracts/mock_splunk.py`) and fixture schema
- `Entities {user?: str, src_ip?: str, src_country?: str (ISO-3166 alpha-2), hosts: list[str] = []}`
- `Alert {seq: int, alert_id, title, splunk_severity: Severity (from contracts.models), summary, rule_name, mitre_techniques: list[str] (pattern ^T\d{4}(\.\d{3})?$), triggered_at, emitted_at, replay_of: str|None, entities: Entities}`
- `SplunkEvidence {evidence_id (pattern ^EV-[A-Z]+-\d{4}$), source, timestamp, alert_ids: list[str], entities: Entities, content: dict[str, Any]}`
- `AlertPage`, `EvidenceList`, `SourceList`, `ResetResponse`, `ReplayRequest`. All use `extra="forbid"`, as in C1's `APIModel`.

Fixture files live in `services/mock_splunk/fixtures/`:
- `alerts.json`: `{"seed_version": "c2-seed-1", "alerts": [<Alert without seq, emitted_at or replay_of>]}`. `seq` is assigned in file order starting at 1, and `emitted_at = triggered_at`.
- `<source>.json`: `{"source": "<name>", "records": [<SplunkEvidence without source>]}`. Each record's `content` must validate against that source's `Record` model (§4). The `evidence_id` prefix must equal the source's `id_prefix`.
- The loader fails fast (it raises at startup) on invalid fixtures, duplicate evidence ids, or an `alert_ids` entry that doesn't exist in `alerts.json`.

## 3. Seed scenario (exact values; all times UTC)
**Alert** `SPL-2026-0001`, seq 1
- title "Privileged account login from new country: adm.mtan"; `splunk_severity: "medium"`; rule_name "Privileged Login From New Country"
- `mitre_techniques: ["T1078", "T1021"]`; `triggered_at: 2026-11-18T02:04:00Z`
- summary "Domain admin adm.mtan authenticated via VPN-GW-01 from RO (203.0.113.45); account baseline is SG."
- entities `{user: "adm.mtan", src_ip: "203.0.113.45", src_country: "RO", hosts: ["VPN-GW-01","JMP-01","FIN-DB-01"]}`

**Evidence** (8 records: auth 3, proxy 2, edr 3)

| evidence_id | source | timestamp | alert_ids | key content |
|---|---|---|---|---|
| EV-AUTH-0001 | auth | 2026-11-17T01:12:05Z | [] (baseline) | success, user adm.mtan, src 198.51.100.23, country SG, mfa approved |
| EV-AUTH-0002 | auth | 2026-11-18T01:58:12Z | [SPL-2026-0001] | failure bad_password, src 203.0.113.45, country RO, target VPN-GW-01 |
| EV-AUTH-0003 | auth | 2026-11-18T02:03:41Z | [SPL-2026-0001] | success, mfa push approved, country RO, `first_seen_country: true`, target VPN-GW-01, vpn_ip 10.20.0.5 |
| EV-EDR-0001 | edr | 2026-11-18T02:06:02Z | [SPL-2026-0001] | host JMP-01, event logon, logon_type 10 (RDP), user adm.mtan, from 10.20.0.5 |
| EV-PROXY-0001 | proxy | 2026-11-18T02:07:30Z | [SPL-2026-0001] | client JMP-01, GET https://dl.example.net/tools/rclone.zip, category uncategorized, `first_seen_domain: true`, bytes_in 18874368 |
| EV-EDR-0002 | edr | 2026-11-18T02:12:18Z | [SPL-2026-0001] | host JMP-01, event process, image mstsc.exe, cmdline "mstsc.exe /v:FIN-DB-01", parent explorer.exe |
| EV-EDR-0003 | edr | 2026-11-18T02:12:40Z | [SPL-2026-0001] | host FIN-DB-01, event logon, logon_type 10, user adm.mtan, from JMP-01 (10.10.5.21) |
| EV-PROXY-0002 | proxy | 2026-11-18T02:20:11Z | [SPL-2026-0001] | client FIN-DB-01, POST https://dl.example.net/upload, bytes_out 48234496 |

The IPs and domain come from documentation-reserved ranges (TEST-NET, example.net). The story is T1078 (valid privileged account from a new country), followed by T1021 (RDP from JMP-01 to FIN-DB-01). The data supports a reassessment above "medium", but the fixtures **do not contain a verdict**. Triage must infer it (brief §9, Scenario C).
Key reference counts for tests: `/v1/alerts/SPL-2026-0001/evidence` returns 7 records. The window `source=auth&start=2026-11-17T00:00:00Z&end=2026-11-19T00:00:00Z&entity=adm.mtan` returns 3 (it includes the baseline).

## 4. Source plug-in recipe (adding e.g. VPN logs)
Base, in `services/mock_splunk/sources/base.py`: `class Source(Protocol)` with `name: str`, `id_prefix: str`, `Record: type[BaseModel]` and `fixture: str`.
To add `vpn`, change exactly these three places and nothing else:
1. **New file** `services/mock_splunk/sources/vpn.py`:
   ```python
   class VpnRecord(BaseModel):
       model_config = ConfigDict(extra="forbid")
       gateway: str; user: str; action: Literal["connect", "disconnect"]; assigned_ip: str
   SOURCE = SourceSpec(name="vpn", id_prefix="EV-VPN-", Record=VpnRecord, fixture="vpn.json")
   ```
2. **New file** `services/mock_splunk/fixtures/vpn.json`: `{"source":"vpn","records":[...]}`
3. **One line** in `services/mock_splunk/sources/__init__.py`: add `vpn.SOURCE` to `REGISTRY = {s.name: s for s in (auth.SOURCE, proxy.SOURCE, edr.SOURCE)}`.

No route, model, spec or test edits are needed. The registry-parametrized tests (`test_sources.py`) cover the new source automatically, and the OpenAPI stays byte-identical. `test_adding_source_needs_no_spec_change` proves this with a temporary test-only source.

## 5. Ingest mapping (`contracts/ingest_mapping.py`, pure, no I/O)
- `alert_to_case_create(alert: Alert, actor: Actor) -> CaseCreate` takes `alert_id`, `title`, `splunk_severity` and `summary` one to one. `assigned_analyst` is left None.
- `evidence_to_evidence_create(ev: SplunkEvidence, actor: Actor) -> EvidenceCreate` sets `source = ev.source` and `ref = ev.evidence_id`, and puts this in `content`: `{"timestamp", "alert_ids", "entities", **ev.content}`. A record field that collides with one of those keys raises `ValueError` and is caught by a test.
- MITRE ids and `triggered_at` don't fit C1's `CaseCreate` (`extra="forbid"`). C3 should put them in the triage rationale or in evidence. Adding them to `CaseCreate` would be a separate additive C1 change, and that's out of scope here.

## 6. C1 follow-ups (in the same run)
1. In `services/case_api/routes.py`, change `responses=_errors(404)` to `_errors(404, 422)` on the 4 GET routes `get_case`, `get_evidence`, `get_case_history` and `get_case_view` (a non-int `case_id` gives 422). Regenerate `contracts/openapi.json`. This is additive only.
2. Add these tests in `tests/case_api/test_audit.py`:
   - `test_reject_writes_exactly_one_disposition_rejected_event`
   - `test_decision_missing_rationale_422_writes_no_event`
   - `test_mutating_call_missing_actor_422_writes_no_event` (parametrized over the 7 POSTs)
3. Add `tests/case_api/test_api.py::test_get_routes_non_int_id_returns_422` (parametrized over the 4 GETs).

## 7. File layout (new, or changed with ~)
```
contracts/mock_splunk.py              # Alert, SplunkEvidence, pages, Reset/Replay models
contracts/ingest_mapping.py           # §5
contracts/mock_splunk.openapi.json    # GENERATED, committed
contracts/C2-contract.md              # this file
services/mock_splunk/__init__.py
services/mock_splunk/app.py           # create_app(fixtures_dir=None, clock=None); no logic
services/mock_splunk/routes.py        # thin, reuses C1's ErrorResponse + error mapping style
services/mock_splunk/store.py         # SplunkStore: load/validate fixtures, query, reset, replay
services/mock_splunk/errors.py
services/mock_splunk/sources/{__init__,base,auth,proxy,edr}.py
services/mock_splunk/fixtures/{alerts,auth,proxy,edr}.json
~scripts/export_openapi.py            # SPECS = {"contracts/openapi.json": case_api, "contracts/mock_splunk.openapi.json": mock_splunk}; same CLI, --check covers both
~services/case_api/routes.py          # §6.1
~contracts/openapi.json               # regenerated
~.cursor/rules/architecture.mdc       # add: Mock Splunk is read-only and holds no case logic; new sources follow §4 only
~README.md                            # run: uvicorn services.mock_splunk.app:app --port 8001; reset via POST /v1/reset
tests/mock_splunk/{__init__,conftest}.py   # fixed clock, TestClient
tests/mock_splunk/test_fixtures.py
tests/mock_splunk/test_api.py
tests/mock_splunk/test_sources.py
tests/test_ingest_mapping.py
~tests/test_openapi_drift.py          # parametrize over SPECS
~tests/case_api/test_audit.py, test_api.py   # §6
```
CI (`.github/workflows/ci.yml`) is unchanged. The ruff, mypy, pytest and `export_openapi.py --check` jobs already cover the new code (mypy `packages` already includes `services` and `contracts`).

## 8. Named pytest cases by requirement
**Deterministic, schema-valid fixtures**
- `test_fixtures.py::test_all_fixtures_validate_against_models`
- `test_fixtures.py::test_evidence_ids_unique_and_prefixed_by_source`
- `test_fixtures.py::test_alert_ids_referenced_by_evidence_exist`
- `test_fixtures.py::test_loader_rejects_invalid_fixture` (uses a tmp fixtures dir)
- `test_api.py::test_responses_identical_across_two_app_instances`

**Seed scenario**
- `test_api.py::test_seed_alert_values` (id, medium, T1078 and T1021, triggered_at, entities)
- `test_api.py::test_seed_alert_evidence_returns_7_sorted_by_time`
- `test_api.py::test_evidence_by_alert_filtered_by_source` (edr gives 3)

**Alerts feed**
- `test_api.py::test_list_alerts_paginates_with_after_seq`
- `test_api.py::test_unknown_alert_returns_404`

**Evidence by time window and source**
- `test_api.py::test_window_includes_baseline_login` (the 3 auth records in §3)
- `test_api.py::test_window_is_start_inclusive_end_exclusive`
- `test_api.py::test_window_unknown_source_returns_422`
- `test_api.py::test_window_start_after_end_returns_422`
- `test_api.py::test_window_over_7_days_returns_422`
- `test_api.py::test_window_naive_timestamp_returns_422`

**Reset**
- `test_api.py::test_reset_restores_seed_after_replay`
- `test_api.py::test_reset_is_idempotent`

**Replay**
- `test_api.py::test_replay_emits_new_alert_with_suffix_and_next_seq`
- `test_api.py::test_replay_uses_injected_clock_for_emitted_at`
- `test_api.py::test_replayed_alert_evidence_matches_root`
- `test_api.py::test_replay_of_replay_resolves_to_root`
- `test_api.py::test_replay_unknown_alert_returns_404`

**Pluggable sources**
- `test_sources.py::test_each_registered_source_has_fixture_and_valid_records` (parametrized over REGISTRY)
- `test_sources.py::test_sources_endpoint_lists_registry`
- `test_sources.py::test_adding_source_needs_no_spec_change`

**Alignment with C1**
- `test_ingest_mapping.py::test_alert_maps_to_valid_case_create`
- `test_ingest_mapping.py::test_evidence_maps_ref_to_evidence_id`
- `test_ingest_mapping.py::test_seed_ingests_into_case_api_end_to_end` (in-process: both apps' TestClients, ingest the alert and 7 evidence records, then the C1 history shows 1 `case.ingested` and 7 `evidence.added`)
- `test_ingest_mapping.py::test_content_key_collision_raises`

**OpenAPI drift**
- `test_openapi_drift.py::test_committed_openapi_matches_app[case_api]`
- `test_openapi_drift.py::test_committed_openapi_matches_app[mock_splunk]`

**C1 follow-ups:** the tests listed in §6.

Done means: CI is green, every test above passes, and both spec files are regenerated and committed.

## 9. Out of scope and limits
- Out of scope: continuous random alert generation (brief §9 says to prefer seeded data), Scenarios A and B as separate fixtures, auth, persistence across restarts, the LangGraph ingest itself (C3), and Mock SOAR.
- Limit: state is per process, so a restart behaves like a reset.
- Budget fit: about 20 small files, mostly fixtures and thin routes. If the run is tight, drop `GET /v1/sources` and `test_adding_source_needs_no_spec_change` first.
