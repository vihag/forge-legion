# C1 Contract: Case Store and Case API (/v1)

Owner: FORGE Architecture. Next: FORGE Implementation, then FORGE QA. Sources: brief v0.4 §8, §10, §11.4, and task C1 from FORGE Lead.
Stack: Python 3.11, FastAPI, Pydantic v2, stdlib `sqlite3`. Docker is not used: everything runs with `pip install -e .[dev]` and `pytest`.

## 0. Design decisions (why it looks like this)
1. **All business rules live in `services/case_api/service.py`.** That covers transitions, approval checks, duplicate guards and audit writes. Routes only parse, call the service and map errors. Bots and UIs never decide state, they only call the API.
2. **The transition table is one pure dict in `state.py`.** It is unit-testable without HTTP or a database.
3. **Each mutating call is one SQLite transaction.** The state change and its audit event commit together or not at all. A rejected call (any 4xx) writes no event.
4. **Every mutating request carries an `actor`** with `{id, type: analyst|agent|system}`. Only `type=analyst` may decide (approve or reject). This is an honest gate, not authentication, which is out of scope for v1 (see §9).
5. **The SOAR call sits behind a `SoarClient` protocol.** C1 ships `FakeSoarClient`, which returns a deterministic `SOAR-<case_id>-0001`. Mock SOAR (a later task) plugs in through config, with no API change.
6. **Beyond the 8 logical ops (flagged for Lead):** C1 adds 3 small write endpoints, `POST /v1/cases`, `POST /v1/cases/{id}/triage` and `POST /v1/cases/{id}/evidence`. Without them, LangGraph and Evidence SENTINEL have no way to write ingestion, triage and evidence. §10 also requires those as audit events. All three are additive. Lead can cut them and defer them to C2.

## 1. Endpoints
All paths are under `/v1`. JSON only. Errors use `ErrorResponse {error: str, message: str, details: dict}`.

| # | Logical op | Method & path | Request model | Response model | Errors |
|---|---|---|---|---|---|
| 1 | list_cases | GET `/v1/cases?status=&severity=&limit=50&offset=0` | query | `CaseList {items: CaseSummary[], total}` | 422 |
| 2 | get_case | GET `/v1/cases/{case_id}` | – | `Case` | 404 |
| 3 | get_evidence | GET `/v1/cases/{case_id}/evidence` | – | `Evidence[]` | 404 |
| 4 | record_finding | POST `/v1/cases/{case_id}/findings` | `FindingCreate` | 201 `Finding` | 404, 409, 422 (unknown evidence id; observation with no evidence) |
| 5 | get_case_history | GET `/v1/cases/{case_id}/history` | – | `AuditEvent[]` (ascending) | 404 |
| 6 | propose_disposition | POST `/v1/cases/{case_id}/disposition` | `DispositionCreate` | 201 `Disposition` | 404, 409 (illegal state, or no findings), 422 |
| 7 | approve_disposition | POST `/v1/cases/{case_id}/decision` | `DecisionCreate` | 201 `Decision` | 403 (actor not analyst), 404, 409, 422 (missing analyst or rationale) |
| 8 | submit_to_soar | POST `/v1/cases/{case_id}/soar-submission` | `SoarSubmitRequest` | 201 `SoarSubmission` | 404, 409 (not approved, or already submitted), 502 (SOAR failure; state unchanged) |
| 9 | view | GET `/v1/cases/{case_id}/view` | – | `CaseView` | 404 |
| 10 | events | GET `/v1/events?after_id=0&case_id=&limit=100` | query | `EventPage {items: AuditEvent[], next_after_id}` | 422 |
| 11 | ingest (added) | POST `/v1/cases` | `CaseCreate` | 201 `Case` | 409 (duplicate `alert_id`), 422 |
| 12 | triage (added) | POST `/v1/cases/{case_id}/triage` | `TriageCreate` | 200 `Case` | 404, 409, 422 |
| 13 | add_evidence (added) | POST `/v1/cases/{case_id}/evidence` | `EvidenceCreate` | 201 `Evidence` | 404, 409, 422 |
| – | health | GET `/healthz` (unversioned) | – | `{status:"ok"}` | – |

`/v1/events` is a cursor-polled stream, not SSE or WebSocket. Consumers such as routines and CENTURION poll it with `after_id`.

## 2. States and transitions
States (`CaseStatus` enum): `NEW`, `TRIAGED`, `INVESTIGATING`, `DISPOSITION_PROPOSED`, `APPROVED`, `REJECTED`, `SUBMITTED`.

| Operation | Allowed from | To |
|---|---|---|
| ingest | (no case) | NEW |
| triage | NEW, TRIAGED (re-assessment, Scenario C) | TRIAGED |
| add_evidence | NEW, TRIAGED, INVESTIGATING, REJECTED | (unchanged) |
| record_finding | TRIAGED, INVESTIGATING, REJECTED | INVESTIGATING |
| propose_disposition | INVESTIGATING (needs ≥1 finding) | DISPOSITION_PROPOSED |
| decision=approve | DISPOSITION_PROPOSED | APPROVED |
| decision=reject | DISPOSITION_PROPOSED | REJECTED |
| submit_to_soar | APPROVED (no prior submission) | SUBMITTED |

Any other combination returns **409** `{error:"illegal_transition", details:{operation, from_status, allowed_from}}`. SUBMITTED is terminal. A rejected case goes back to investigation through a new finding, then needs a fresh proposal and a fresh decision. A proposal is never an approval, and an approval is never a submission (brief §8).

## 3. Data model
### Pydantic (in `contracts/models.py`, the single source of truth)
- `Actor {id: str(1..64), type: Literal["analyst","agent","system"]}`
- `Severity = Literal["low","medium","high","critical"]`
- `CaseCreate {alert_id, title, splunk_severity: Severity, summary?, assigned_analyst?, actor}`
- `TriageCreate {severity: Severity, rationale, model: str, actor}` (model is the Grok model name that produced it)
- `EvidenceCreate {source: str (e.g. "mock_splunk"), ref: str, content: dict, actor}`
- `FindingCreate {kind: Literal["observation","hypothesis"], statement, evidence_ids: list[int], confidence?: float 0..1, actor}`. Rule: `observation` needs ≥1 evidence id, and every id must belong to this case.
- `DispositionCreate {action: Literal["false_positive","benign","escalate","contain"], rationale, finding_ids: list[int] (≥1), actor}`
- `DecisionCreate {decision: Literal["approve","reject"], rationale: str(min 1), actor}`. `actor.type` must be `analyst`. The decision binds to the current open disposition.
- `SoarSubmitRequest {actor}`
- Read models: `Case`, `CaseSummary`, `Evidence`, `Finding`, `Disposition`, `Decision`, `SoarSubmission`, and `AuditEvent` (§4).
- `CaseView {id, title, severity (triage severity if present, else Splunk), splunk_severity, status, summary, assigned_analyst, evidence: Evidence[], findings: Finding[] (each has kind and evidence_ids), proposed_disposition: Disposition|null, decision: Decision|null, soar: SoarSubmission|null, audit: AuditEvent[]}`

### SQLite (`db/schema.sql`, applied idempotently on startup with `CREATE TABLE IF NOT EXISTS`)
- `cases(id INTEGER PK, alert_id TEXT UNIQUE NOT NULL, title, summary, splunk_severity, triage_severity NULL, triage_rationale NULL, triage_model NULL, assigned_analyst NULL, status NOT NULL, created_at, updated_at)`
- `evidence(id PK, case_id FK, source, ref, content_json, added_by, created_at)`
- `findings(id PK, case_id FK, kind CHECK IN ('observation','hypothesis'), statement, confidence NULL, author_id, author_type, created_at)`
- `finding_evidence(finding_id FK, evidence_id FK, PK(finding_id, evidence_id))`
- `dispositions(id PK, case_id FK, action, rationale, finding_ids_json, proposed_by, created_at, superseded INTEGER DEFAULT 0)`
- `decisions(id PK, case_id FK, disposition_id FK, decision, rationale, analyst_id, created_at)`
- `soar_submissions(id PK, case_id FK UNIQUE, disposition_id FK, soar_case_id, submitted_by, created_at)`. The UNIQUE constraint is the second guard against duplicates.
- `audit_events(id INTEGER PK AUTOINCREMENT, case_id NULL FK, ts, type, actor_id, actor_type, component, from_status NULL, to_status NULL, payload_json)`

Times are ISO-8601 UTC with a `Z` suffix. The DB path comes from env `CASE_DB_PATH` (default `./data/cases.db`). Tests use `tmp_path`. Reset is `python -m services.case_api.reset`, which drops and recreates the tables.

## 4. Audit event shape
```json
{"id": 42, "case_id": 7, "ts": "2026-11-19T05:00:00Z", "type": "disposition.proposed",
 "actor": {"id": "disposition-sentinel", "type": "agent"}, "component": "case_api",
 "from_status": "INVESTIGATING", "to_status": "DISPOSITION_PROPOSED",
 "payload": {"disposition_id": 3, "action": "contain"}}
```
Types, one per successful mutating call: `case.ingested`, `case.triaged`, `evidence.added`, `finding.recorded`, `disposition.proposed`, `disposition.approved`, `disposition.rejected`, `soar.submitted`. Payloads carry ids and key fields only, never the full evidence content. `id` increases monotonically and is the `/v1/events` cursor.

## 5. File layout
```
pyproject.toml                 # deps + ruff, mypy (strict on services/ and contracts/), pytest config
contracts/__init__.py
contracts/models.py            # all Pydantic models + enums (shared with bots/LangGraph later)
contracts/openapi.json         # GENERATED, committed
contracts/C1-contract.md       # this file
db/schema.sql
scripts/export_openapi.py      # writes contracts/openapi.json; --check exits 1 on drift
services/__init__.py
services/case_api/__init__.py
services/case_api/app.py       # create_app(db_path, soar_client) factory; no logic
services/case_api/routes.py    # thin handlers -> service; error mapping
services/case_api/service.py   # CaseService: transitions, validation, audit (all rules)
services/case_api/state.py     # TRANSITIONS table + check_transition() (pure)
services/case_api/store.py     # sqlite repository, transaction context manager
services/case_api/soar.py      # SoarClient Protocol + FakeSoarClient
services/case_api/errors.py    # NotFound, IllegalTransition, Forbidden, Duplicate
services/case_api/reset.py
tests/case_api/conftest.py     # tmp db, TestClient, seeded-case fixtures
tests/case_api/test_state.py
tests/case_api/test_service.py
tests/case_api/test_api.py
tests/case_api/test_audit.py
tests/test_openapi_drift.py
.cursor/rules/architecture.mdc # the 3 guardrails below, always-apply
.github/workflows/ci.yml       # py3.11: pip install -e .[dev]; ruff check .; mypy; pytest; python scripts/export_openapi.py --check
README.md                      # add setup, run (uvicorn services.case_api.app:app), reset, test
```
The rules in `.cursor/rules/architecture.mdc`: (1) no business logic in bots or UIs, since state rules live only in `service.py`. (2) `/v1` is backward compatible: additive changes only, and never remove or rename a field, status or path. Breaking changes go to `/v2`. (3) Modules stay independently testable: `state.py` has no I/O, `service.py` takes a store and a soar client by injection, and routes hold no logic.

## 6. Acceptance tests (named pytest cases)
**AC1: 409 on illegal transition**
- `test_state.py::test_transition_table_allows_only_documented_moves` (parametrized over every op × status pair, checked against §2)
- `test_api.py::test_record_finding_on_new_case_returns_409`
- `test_api.py::test_propose_without_findings_returns_409`
- `test_api.py::test_decision_when_not_proposed_returns_409`
- `test_api.py::test_submit_before_approval_returns_409`
- `test_api.py::test_submit_twice_returns_409_and_single_submission_row`
- `test_api.py::test_illegal_transition_body_names_from_status_and_allowed`

**AC2: Approve requires an analyst decision**
- `test_api.py::test_approve_by_agent_actor_returns_403`
- `test_api.py::test_decision_without_rationale_returns_422`
- `test_api.py::test_proposal_alone_does_not_approve` (status stays DISPOSITION_PROPOSED, soar submit gives 409)
- `test_api.py::test_analyst_approve_then_submit_returns_soar_id`
- `test_api.py::test_reject_then_new_finding_requires_fresh_proposal_and_decision`

**AC3: An audit event on every mutating call**
- `test_audit.py::test_each_mutating_endpoint_writes_exactly_one_event` (parametrized over the 7 POSTs, checking type, actor, from/to status and ts)
- `test_audit.py::test_rejected_call_writes_no_event`
- `test_audit.py::test_get_endpoints_write_no_event`
- `test_audit.py::test_happy_path_history_is_ordered` (ingested → triaged → evidence.added → finding.recorded → disposition.proposed → disposition.approved → soar.submitted)
- `test_audit.py::test_events_stream_paginates_with_after_id`

**Supporting contract checks**
- `test_api.py::test_observation_without_evidence_returns_422`
- `test_api.py::test_finding_with_foreign_evidence_id_returns_422`
- `test_api.py::test_view_returns_typed_findings_with_evidence_ids`
- `test_service.py::test_service_runs_without_http` (service plus in-memory store plus FakeSoar)
- `test_openapi_drift.py::test_committed_openapi_matches_app`

Done means: CI is green (ruff, mypy, pytest, OpenAPI check), every test above passes, and the OpenAPI in `contracts/openapi.json` lists all of §1.

## 7. Out of scope for C1
Real Mock SOAR and Mock Splunk services, the LangGraph triage, bot connectors, auth, SSE or WebSocket, Docker, and concurrency beyond SQLite's defaults.

## 8. Budget fit
This is about 14 files of ordinary CRUD plus one transition table. It should fit one Cursor run. If the run is tight, cut the 3 added endpoints (§0.6) and keep the seeding in a test fixture.

## 9. Known limitations to state in the demo
- The actor `type` is self-declared, so the 403 shows the rule but does not authenticate anyone.
- Rejected attempts are not audited.
- `FakeSoarClient` is synthetic by design (brief §10: no real remediation).
