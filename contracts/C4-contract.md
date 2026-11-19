# C4 Contract: Mock SOAR + MCP adapter

Owner: FORGE Architecture. Next: FORGE Implementation, then FORGE QA. Base: `main` at 01aab7d. Sources: brief v0.4 §10, §7, §4, and contracts C1 and C3.
Stack: Python 3.11, FastAPI, Pydantic v2, stdlib `sqlite3` and the official `mcp` Python SDK. There is no Docker, and CI makes no network calls.

## 0. Design decisions
1. **There are two approval gates, and the Case API owns the real one.** The Case API only calls SOAR from `APPROVED`, and only an analyst can reach `APPROVED` (C1). Mock SOAR re-checks the approval *payload* it receives as defense in depth. It never calls back into the Case API, because the Case API holds a SQLite write transaction during the SOAR call.
2. **Idempotency lives in Mock SOAR, and C1's external 409 stays.** The risk being fixed is this: if the Case API calls SOAR and then fails before it commits, the transaction rolls back and the case is still `APPROVED`. A retry has to get the **same** SOAR id with no second record. Mock SOAR keys on `case_id` (UNIQUE), so a repeat returns the same id. Once the Case API has committed, a repeat `POST /soar-submission` still returns 409 `duplicate`, and `details.soar_case_id` already carries the id. **There is no /v1 change** and no change to C1 tests. **FORGE Lead ruling:** a second submit after a successful commit returns **409** with `error=duplicate` and `details.soar_case_id` (not `illegal_transition`). The HTTP status is unchanged; this is an accepted compatible refinement of the error body only.
3. **The SoarClient protocol changes internally only.** `submit(case_id)` becomes `submit(request: SoarRequest) -> str`, so SOAR receives the approved action and the approval. This is Python-internal, so OpenAPI is unchanged. `FakeSoarClient` keeps returning `SOAR-<case_id>-0001`. The config `SOAR_CLIENT=fake|http` (default `fake`) and `MOCK_SOAR_URL` select `HttpSoarClient` in `create_app`.
4. **The MCP adapter is a pass-through.** Each tool maps to exactly one Case API HTTP call, with no state, rules or retries. Case API errors come back unchanged as MCP tool errors. Audit comes **only from the Case API's existing events**, and the adapter adds no event types.
5. **Library and transport:** the official `mcp` SDK's `FastMCP` over **streamable HTTP** at `/mcp`. It's the reference MCP implementation, a remote connector can reach streamable HTTP, and the SDK's in-memory client session lets CI run the real protocol with no network.

## 1. Mock SOAR endpoints (`services/mock_soar/`, default port 8002, own DB `SOAR_DB_PATH`, default `./data/soar.db`)
Errors use C1's `ErrorResponse`.

| # | Purpose | Method & path | Request | Response | Errors |
|---|---|---|---|---|---|
| 1 | health | GET `/healthz` | – | `{status:"ok"}` | – |
| 2 | submit | POST `/v1/submissions` | `SoarSubmissionRequest` | **201** `SoarReceipt{created:true}` the first time; **200** `SoarReceipt{created:false}` (same id) on an identical repeat | 409 `conflicting_resubmission` (same case_id, different disposition_id or action); 422 `approval_required` (decision ≠ approve, or approver type ≠ analyst); 422 validation |
| 3 | get by id | GET `/v1/submissions/{soar_case_id}` | – | `SoarRecord` | 404 |
| 4 | list | GET `/v1/submissions?case_id=` | query | `SoarRecordList {items}` | 422 |
| 5 | reset | POST `/v1/reset` | `{}` | `{submissions: 0}` | – |

Models go in `contracts/mock_soar.py`, all with `extra="forbid"`:
- `SoarApproval {decision_id: int, decision: DecisionValue, approver: Actor, decided_at: str}`
- `SoarSubmissionRequest {case_id: int, alert_id: str, disposition_id: int, action: DispositionAction, approval: SoarApproval, submitted_by: Actor}`
- `SoarReceipt {soar_case_id: str, case_id: int, created: bool, recorded_at: str}`
- `SoarRecord` (the request fields plus `soar_case_id`, `recorded_at`)

**ID rule:** `soar_case_id = "SOAR-" + zero-padded 6-digit row id`, e.g. `SOAR-000001`. It's assigned once in the same insert, and `UNIQUE(case_id)` is the backstop. This is deterministic after a reset.

## 2. Rules
**Approval:** (a) The Case API requires status `APPROVED`, and only `actor.type=analyst` can approve (C1 §2). (b) Mock SOAR requires `approval.decision == "approve"` and `approval.approver.type == "analyst"`, or it returns 422. (c) The approval sent is the Case API's stored `Decision` for the open disposition. The service builds `SoarRequest` from the store and never from caller input.
**Idempotency:** the first submit for a `case_id` inserts one row and returns 201. A repeat with the same `(case_id, disposition_id, action)` returns 200 with the same id and inserts nothing. A repeat with a different disposition or action returns 409. A crash after the SOAR call but before the Case API commits is recovered by the retry: same id, one SOAR row, one `soar.submitted` event.
**Failure mapping:** any HttpSoarClient transport error or non-2xx raises `SoarError`, which gives C1's existing 502. The case stays `APPROVED`.

## 3. MCP adapter (`services/mcp_adapter/`, ASGI app with `/mcp` mounted, default port 8003, env `CASE_API_URL`)
| Tool | Input (in `contracts/mcp_tools.py`) | Case API call | Mutating | Audit event (Case API, existing) |
|---|---|---|---|---|
| `list_cases` | `status?, severity?, alert_id?, limit=50, offset=0` | GET `/v1/cases` | no | – |
| `get_case` | `case_id` | GET `/v1/cases/{id}/view` | no | – |
| `get_case_history` (added: brief §7 requires reading history) | `case_id` | GET `/v1/cases/{id}/history` | no | – |
| `add_evidence` | `case_id, source, ref, content, actor_id` | POST `/v1/cases/{id}/evidence` | yes | `evidence.added` |
| `record_finding` | `case_id, kind, statement, evidence_ids, confidence?, actor_id` | POST `/v1/cases/{id}/findings` | yes | `finding.recorded` |
| `propose_disposition` | `case_id, action, rationale, finding_ids, actor_id` | POST `/v1/cases/{id}/disposition` | yes | `disposition.proposed` |
| `request_soar_action` | `case_id, actor_id` | POST `/v1/cases/{id}/soar-submission` | yes | `soar.submitted` |

- **No approve or reject tool.** Decisions stay with the analyst through the Case API (brief §6.5).
- **Actor identity:** each mutating tool requires `actor_id` (pattern `^[a-z0-9][a-z0-9-]{0,59}$`, e.g. `evidence-sentinel`). The adapter always sends `actor = {id: "mcp:" + actor_id, type: "agent"}`. The caller can't choose the type, so a bot over MCP is never an analyst. The `mcp:` prefix in the Case API's audit `actor.id` shows the call came through the adapter, with no C1 change. If `MCP_ACTOR_ALLOWLIST` (comma-separated) is set, other ids are rejected as a tool error before any Case API call. That check is input validation, not business logic.
- **Approval gate:** `request_soar_action` before approval gets the Case API's 409. The adapter returns it as `isError: true` with `{status:409, error:"illegal_transition", ...}` exactly as received.
- **Auth:** if `MCP_ADAPTER_TOKEN` is set, the HTTP transport requires `Authorization: Bearer <token>`, otherwise 401. That's required whenever `/mcp` is exposed through a tunnel such as ngrok (brief §7: don't assume reachability). The in-memory CI transport skips it.
- **Errors:** a Case API 4xx or 5xx returns a tool result with `isError: true` and the JSON `{status, error, message, details}`. Case API unreachable returns `{status: 503, error: "case_api_unavailable"}`.

## 4. Audit mapping (brief §10)
| Step | Event | Emitted by | Actor |
|---|---|---|---|
| bot adds evidence via MCP | `evidence.added` | Case API | `mcp:<bot>` / agent |
| bot records finding | `finding.recorded` | Case API | `mcp:<bot>` / agent |
| bot proposes disposition | `disposition.proposed` | Case API | `mcp:<bot>` / agent |
| analyst approves (Case API directly, not MCP) | `disposition.approved` | Case API | analyst |
| bot requests SOAR | `soar.submitted` (payload already has `soar_case_id`) | Case API | `mcp:<bot>` / agent |
| SOAR records it | `soar_submissions` row (Mock SOAR's own record, not a case event) | Mock SOAR | `submitted_by` |

The adapter adds no events, and failed calls add none (C1 rule).

## 5. Contracts and drift
- `contracts/mock_soar.openapi.json` is generated. Add `"mock_soar"` to `SPECS` in `scripts/export_openapi.py`.
- `contracts/mcp_tools.json` is generated: the adapter's `tools/list` output (name, description, inputSchema), sorted by name. The same script writes it, and `--check` fails on drift. `contracts/openapi.json` (Case API) stays byte-identical.

## 6. File layout (new, or changed with ~)
```
contracts/mock_soar.py, contracts/mcp_tools.py
contracts/mock_soar.openapi.json, contracts/mcp_tools.json   # GENERATED
contracts/C4-contract.md
db/mock_soar.sql
services/mock_soar/{__init__,app,routes,service,store,errors,reset}.py
services/mcp_adapter/{__init__,app,server,case_client,config}.py   # server.py = FastMCP tools; app.py = ASGI + bearer check
~services/case_api/soar.py      # SoarRequest, Protocol submit(request), FakeSoarClient(request), HttpSoarClient
~services/case_api/service.py   # build SoarRequest from the stored Decision + open Disposition
~services/case_api/app.py       # SOAR_CLIENT / MOCK_SOAR_URL selection (default fake)
~scripts/export_openapi.py      # + mock_soar spec, + mcp_tools.json
~pyproject.toml                 # + mcp (pinned minor)
~Makefile                       # + run-soar, run-mcp, e2e-soar
~.cursor/rules/architecture.mdc # + MCP adapter: one tool = one Case API call, no logic; approval only via Case API
~README.md
tests/mock_soar/{__init__,conftest,test_api,test_service}.py
tests/mcp_adapter/{__init__,conftest,test_tools,test_auth}.py   # in-memory MCP session; Case API via httpx ASGITransport
tests/test_soar_integration.py
~tests/test_openapi_drift.py    # parametrize adds mock_soar + mcp_tools
```

## 7. Named pytest cases by requirement
**Mock SOAR: validate, record, return an id exactly once**
- `test_api.py::test_first_submit_returns_201_and_soar_id`
- `test_api.py::test_identical_repeat_returns_200_same_id_no_new_row`
- `test_api.py::test_conflicting_resubmission_returns_409`
- `test_api.py::test_reject_decision_returns_422_approval_required`
- `test_api.py::test_non_analyst_approver_returns_422_approval_required`
- `test_api.py::test_reset_clears_submissions`
- `test_service.py::test_soar_ids_sequential_and_deterministic_after_reset`

**Plugging into C1**
- `test_soar_integration.py::test_case_api_uses_http_soar_client_from_config`
- `test_soar_integration.py::test_happy_path_approved_case_gets_mock_soar_id` (Case API, then Mock SOAR in-process; one row, one `soar.submitted`)
- `test_soar_integration.py::test_retry_after_commit_failure_yields_same_id_once` (force a failure after the SOAR call, then retry; same id, 1 SOAR row, 1 event)
- `test_soar_integration.py::test_case_api_second_submit_still_409_with_soar_case_id`
- `test_soar_integration.py::test_soar_down_returns_502_case_stays_approved`
- `test_soar_integration.py::test_soar_request_built_from_stored_decision`
- Existing C1 SOAR tests pass unchanged with `FakeSoarClient`.

**MCP adapter: thin, gated, audited**
- `test_tools.py::test_tools_list_matches_contract` (the 7 tools, no approve tool)
- `test_tools.py::test_each_tool_makes_exactly_one_case_api_call` (parametrized; a recording transport)
- `test_tools.py::test_mutating_tools_write_one_case_api_event_with_mcp_actor` (parametrized over the 4; actor `mcp:<id>` / agent)
- `test_tools.py::test_read_tools_write_no_event`
- `test_tools.py::test_request_soar_before_approval_returns_tool_error_409`
- `test_tools.py::test_request_soar_after_analyst_approval_returns_soar_id`
- `test_tools.py::test_actor_type_cannot_be_overridden`
- `test_tools.py::test_actor_allowlist_rejects_unknown_before_call`
- `test_tools.py::test_case_api_error_passed_through_unchanged`
- `test_tools.py::test_case_api_unreachable_returns_503_tool_error`
- `test_tools.py::test_adapter_has_no_case_api_imports` (AST check: `services/mcp_adapter` imports nothing from `services.case_api`)
- `test_auth.py::test_bearer_required_when_token_set`
- `test_auth.py::test_no_token_configured_allows_local`

**Contracts and drift**
- `test_openapi_drift.py::test_committed_openapi_matches_app[case_api|mock_splunk|mock_soar]`
- `test_openapi_drift.py::test_committed_mcp_tools_match_server`

Done means: CI is green (ruff, mypy, pytest, `export_openapi.py --check`) with no network, every test above passes, and `contracts/openapi.json` is unchanged.

## 8. Out of scope, limits and budget
- Out of scope: wiring the Grok Bot connector to `/mcp` (that's an ARCHON or LEGION step, which needs a tunnel URL plus the token), a CENTURION routine, a real SOAR, and the `get_evidence` tool (`get_case` already returns the evidence).
- Limits: `mcp:` actor ids are self-declared by the calling bot, and the bearer token is the only authentication. SOAR re-checks the payload, not the Case API's database.
- Budget: about 18 small files, which fits one run. If it's tight, cut `GET /v1/submissions?case_id=`, `MCP_ACTOR_ALLOWLIST` and its test first.
