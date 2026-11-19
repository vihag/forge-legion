# C6 contract: analyst decisions from chat, read-only Splunk MCP tools, CaseView fields, #24 hardening

Base: `main` @ a0134b1. Issue: #25 (closes the #24 items listed in §4). Author: FORGE Architecture. No code here.

## 0. Reasoning in brief (for the demo)
- **Decisions stay in the Case API.** The new chat-approval path is a thin second MCP endpoint that calls the existing
  `POST /v1/cases/{id}/decision`. State rules, the analyst-only rule and the one-audit-event rule are reused, not copied.
- **Two credentials, two endpoints.** Agent tools stay on `/mcp` (agent token, actor forced to `type=agent`, no decide tool).
  Decisions live on `/mcp-analyst` with a different token that only CENTURION holds. The endpoint does not exist unless
  `MCP_ANALYST_DECISIONS_ENABLED=1`. The agent tool list (`mcp_tools.json` for `/mcp`) gains only the read-only Splunk tools.
- **Provenance in the audit trail.** Every chat decision carries the source chat reference and the analyst's verbatim words,
  stored in the existing `disposition.approved|rejected` audit payload. Honest limit: a bot holding the token could still
  fabricate text; the gate makes it deliberate and the chat reference makes it checkable against the transcript.
- **Public exposure: only MCP goes public.** Picked over "bearer on every route" because the bots only need MCP, it keeps auth
  in one place (the adapter) instead of duplicating it in nginx, and the mock Case/Splunk/SOAR APIs stay loopback-only.
- **/v1 stays backward compatible.** All schema changes are additive optional fields; OpenAPI is regenerated.

## 1. Affected files
| File | Change |
|---|---|
| `contracts/models.py` | `DecisionSource` (new), `DecisionCreate.source` (optional), `CaseView` + `alert_id`, `created_at`, `updated_at` |
| `services/case_api/service.py` | decision path validates `source` (§2.3) and writes it into the existing audit payload |
| `services/case_api/routes.py` | none beyond the model change (same route, same status codes plus one 422 code) |
| `services/mcp_adapter/config.py` | new keys (§5); default allowed hosts add bare `localhost`, `127.0.0.1`, `[::1]` |
| `services/mcp_adapter/server.py` | 3 read-only Splunk tools on the agent server |
| `services/mcp_adapter/analyst_server.py` (new) | `build_analyst_mcp_server()` with the single tool `record_analyst_decision` |
| `services/mcp_adapter/splunk_client.py` (new) | httpx client for Mock Splunk GETs only (no POST method exists on the class) |
| `services/mcp_adapter/app.py` | mount `/mcp-analyst` only when enabled; own bearer check; both FastMCP session managers in lifespan; `_NormalizeMcpPath` also maps `POST /mcp-analyst` to `/mcp-analyst/`; public-request fail-closed (§4.1) |
| `deploy/nginx/default.conf.template` | second `server` block on `${PUBLIC_GATEWAY_PORT}` (MCP only); `/mcp-analyst` locations on the local block |
| `deploy/compose.yml` | ngrok targets `nginx:${PUBLIC_GATEWAY_PORT}`; PUBLIC_GATEWAY_PORT not published to the host; healthchecks via `python -m services.healthcheck` |
| `services/healthcheck.py` (new) | `python -m services.healthcheck PORT_ENV_NAME` probes loopback `/healthz`; replaces the `'http'+'://'` hack (#24) |
| `Makefile` | `health` precedence (§4.3), `reset` grouping (#24), `env-merge`, `gen-secret` targets |
| `scripts/env_merge.py` (new) | adds keys missing from `$(ENV_FILE)` using `.env.example` values; never overwrites; prints key names only |
| `scripts/gen_secret.py` (new) | writes a random 64-hex value into `$(ENV_FILE)` for one key if empty; prints the key name only, never the value |
| `scripts/mcp_call.py` (new) | MCP SDK client: `--path /mcp|/mcp-analyst --token-env NAME --tool T --args JSON`; exit 0 ok, 3 tool error, 4 HTTP/auth error; prints result JSON |
| `scripts/health.py` | reads timeout per §4.3 (`--timeout` flag first) |
| `scripts/export_openapi.py`, `openapi/*.json`, `openapi/mcp_tools.json`, `openapi/mcp_analyst_tools.json` (new) | regenerate; `--check` covers the new file |
| `.env.example` | new keys (§5) |
| `pyproject.toml`, `scripts/__init__.py` | drop `scripts*` package entry and the `__init__.py` (#24) |
| `.github/workflows/ci.yml` | boot-smoke adds the public-port checks (§6.3) |
| `contracts/C5-contract.md` (repo copy) | fix the stale §2 env_file, §4 example (ENV_FILE, ROUTING_CONFIG), §5 COMPOSE formula (#24) |
| `README.md` | "`make up-public` is the only supported public path"; GROK_CLIENT note (§4.4) |
| tests | §6 |

## 2. Analyst decision from chat (scope a)

### 2.1 Models (additive)
```python
class DecisionSource(APIModel):              # extra="forbid"
    channel: Literal["grok_bot_chat"]
    chat_ref: str = Field(min_length=1, max_length=200)        # chat/agent id the analyst typed in
    message_ref: str | None = Field(default=None, max_length=200)  # message address, if known
    instruction_text: str = Field(min_length=1, max_length=2000)   # analyst's words, verbatim
    relayed_by: str = Field(min_length=1, max_length=100)      # e.g. "mcp:centurion"

class DecisionCreate(APIModel):
    decision: DecisionValue
    rationale: str = Field(min_length=1)
    actor: Actor
    source: DecisionSource | None = None      # NEW, optional => old clients unchanged
```

### 2.2 MCP endpoint `/mcp-analyst` and its one tool
`record_analyst_decision(case_id: int, decision: "approve"|"reject", analyst_id: str, rationale: str,
chat_ref: str, instruction_text: str, relayed_by: str, message_ref: str | None = None)`

Adapter behaviour, in order (each failure makes **zero** Case API calls):
1. Endpoint mounted only if `MCP_ANALYST_DECISIONS_ENABLED=1`; otherwise `/mcp-analyst` is 404.
2. Startup fails (non-zero exit, message names the key) if enabled and `MCP_ANALYST_TOKEN` is empty, or equals `MCP_ADAPTER_TOKEN`.
3. Bearer must equal `MCP_ANALYST_TOKEN` (constant-time compare), else HTTP 401. The agent token gets 401 here.
4. `analyst_id` must be in `MCP_ANALYST_ALLOWLIST` (comma list, default `vihag`), else tool error `analyst_not_allowed`.
5. `chat_ref`, `instruction_text`, `relayed_by` blank after strip → tool error `source_required`.
6. Exactly one call: `POST /v1/cases/{id}/decision` with
   `actor={id: analyst_id, type: "analyst"}` and `source={channel:"grok_bot_chat", chat_ref, message_ref, instruction_text, relayed_by: "mcp:"+relayed_by}`.
7. Case API errors map through the existing `_map_client_error` (409 → `illegal_transition`, etc.).

The agent `/mcp` server is untouched apart from §3: still no approve/reject tool, actor still forced to `type=agent`.

### 2.3 Case API rules (business logic lives here)
- Unchanged: analyst-only (403 for agent), only from DISPOSITION_PROPOSED (409 otherwise), one audit event in the same transaction.
- New, only when `source` is present: `instruction_text` (case-insensitive) must contain `approve` for an approve or
  `reject` for a reject, else 422 `{"code":"instruction_mismatch"}` and no audit event. A deliberately strict check:
  "yes go ahead" is refused, so the analyst has to say the decision word.
- Audit payload for `disposition.approved|rejected` becomes
  `{"decision_id", "disposition_id", "source": {...}}`; `source` key present only when supplied. No new event type.

## 3. Read-only Splunk MCP tools (scope b), on agent `/mcp`
| Tool | Calls (Mock Splunk, `MOCK_SPLUNK_URL`) |
|---|---|
| `get_alert(alert_id)` | `GET /v1/alerts/{alert_id}` |
| `list_alert_evidence(alert_id, source=None)` | `GET /v1/alerts/{alert_id}/evidence` |
| `search_evidence(source, start=None, end=None, entity=None)` | `GET /v1/evidence` (omitted bounds default to the last 72 h up to now + 1 min) |
- No `actor_id` argument (reads need no actor); actor rules for write tools unchanged.
- `SplunkClient` exposes GET methods only; AST test: adapter never imports `services.mock_splunk` and `splunk_client.py` contains no `.post(`/`.put(`/`.delete(`.
- Errors: Splunk 404 → tool error `not_found`; unreachable → `splunk_unreachable`.
- `mcp_tools.json` regenerated: 7 existing + 3 new = 10 tools, none named approve/reject/decide.

## 4. CaseView and #24 hardening

### 4.0 CaseView (scope c)
Add `alert_id: str`, `created_at: str`, `updated_at: str` (copied from the Case row, same ISO-8601 UTC format as `Case`).
Regenerate `openapi/case_api.json`; drift check must pass.

### 4.1 Public exposure limited to MCP (scope d, pick + reason in §0)
- nginx second server block `listen ${PUBLIC_GATEWAY_PORT}` (default 8081) serves only: `= /healthz`, `= /mcp`, `/mcp/`,
  `= /mcp-analyst`, `/mcp-analyst/`, `= /mcp/healthz`. Everything else `return 404`. It sets `X-Forge-Public: 1` upstream.
- Local block (`GATEWAY_PORT`, 8080) unchanged routes, plus `/mcp-analyst` locations, and sets `X-Forge-Public ""`.
- ngrok: `nginx:${PUBLIC_GATEWAY_PORT}`. `PUBLIC_GATEWAY_PORT` is not in `ports:`.
- Adapter fail-closed (from the #24 comment): a request with `X-Forge-Public: 1` while `MCP_ADAPTER_TOKEN` is empty gets
  503 `{"code":"public_requires_token"}`. Covers `docker compose --profile ngrok up` run directly with an empty token.

### 4.2 Bare `Host: localhost`
Default `MCP_ALLOWED_HOSTS` adds `localhost`, `127.0.0.1`, `[::1]` (no port) next to the existing `:*` forms.
`evil.example` still 421.

### 4.3 HEALTH_TIMEOUT_S precedence
`--timeout` CLI flag > value in the caller's shell/`make` command line > `$(ENV_FILE)` > default 60.
Makefile `health` captures `HEALTH_TIMEOUT_S` before sourcing `$(ENV_FILE)` (not a hard-coded `.env.local`) and re-exports it if it was set.

### 4.4 Other #24 items (small, in this PR)
- `reset`: group each step so a step-1 failure prints one message and exits non-zero.
- Healthchecks: `python -m services.healthcheck CASE_API_PORT` etc.; the no-URL-in-compose test drops its workaround.
- Remove `scripts*` packaging and `scripts/__init__.py`.
- GROK_CLIENT: keep `http` as the default (a silent fixture default would fake the model in the demo); README documents
  `GROK_CLIENT=fixture` for keyless runs. Add the missing test: unknown `GROK_CLIENT` with a key set exits non-zero.
- Fix the stale C5 contract text listed in #24.

## 5. Env keys (merged into the existing `.env.local` only)
| Key | `.env.example` value | Notes |
|---|---|---|
| `MCP_ANALYST_DECISIONS_ENABLED` | `0` | off by default |
| `MCP_ANALYST_TOKEN` | *(empty)* | set by `make gen-secret KEY=MCP_ANALYST_TOKEN`; given to CENTURION only |
| `MCP_ANALYST_ALLOWLIST` | `vihag` | analyst ids allowed to decide |
| `PUBLIC_GATEWAY_PORT` | `8081` | internal only |
| `MOCK_SPLUNK_URL` (adapter) | `http://mock_splunk:8002` | reuse the existing value if present |
Never `cp .env.example .env.local`. `make env-merge` adds only missing keys and prints their names.

## 6. Acceptance criteria and tests (all in-process; CI runs the compose smoke)
### 6.1 Analyst decision (`tests/mcp_adapter/test_analyst.py`, `tests/case_api/test_decision_source.py`)
- A1 disabled flag → `/mcp-analyst` 404; `/mcp` tools/list has no decision tool.
- A2 enabled + empty analyst token → app startup raises; enabled + analyst token == agent token → raises.
- A3 agent token on `/mcp-analyst` → 401; no Case API call (fake client call count 0).
- A4 missing/blank `chat_ref` or `instruction_text` → `source_required`; 0 calls.
- A5 `analyst_id="mallory"` → `analyst_not_allowed`; 0 calls.
- A6 case in TRIAGED → 409 `illegal_transition`; audit count unchanged.
- A7 happy approve from DISPOSITION_PROPOSED → status APPROVED; exactly +1 audit event, type `disposition.approved`,
  actor `{id:"vihag",type:"analyst"}`, payload.source.instruction_text equals input verbatim, relayed_by `mcp:centurion`.
- A8 reject with text "approve it" → 422 `instruction_mismatch`; 0 audit events.
- A9 second decision on the same case → 409; still exactly one decision event.
- A10 old-style `DecisionCreate` without `source` still 200; payload has no `source` key (backward compat).
- A11 agent actor with a `source` → still 403.
### 6.2 Splunk tools, CaseView, hosts, health
- B1 `get_alert("SPL-2026-0001")` returns the seed alert; `list_alert_evidence` returns 7 records; `search_evidence` with
  source=auth over the baseline window returns EV-AUTH-0001.
- B2 Mock Splunk request log after B1 has only GETs; AST/no-write test passes.
- B3 `get_alert("NOPE")` → `not_found`.
- B4 `GET /v1/cases/{id}/view` has `alert_id`, `created_at`, `updated_at`; `export_openapi.py --check` clean.
- B5 Host `localhost` → 200; `localhost:8080` → 200; `evil.example` → 421.
- B6 `X-Forge-Public: 1` with no adapter token → 503 `public_requires_token`; with token configured and bearer → 200.
- B7 `HEALTH_TIMEOUT_S=5` in env beats `.env.local=60`; `--timeout 2` beats both (unit test on the resolver).
### 6.3 CI boot-smoke additions
- From inside the network: `http://nginx:8081/case/v1/cases` → 404, `/splunk/v1/alerts` → 404, `/mcp` without bearer → 401 (CI sets a token).
- `http://nginx:8080/case/v1/cases` → 200 (local block unchanged).

## 7. Laptop verification (ARCHON runs these, in `/Users/vihaggupta/forge-legion`)
No token values are printed or pasted. Tokens are loaded into the shell from `.env.local` and passed by env-var name.
```bash
git pull && git log --oneline -1                    # C6 merge commit
make env-merge                                      # L1: prints only added key names
make gen-secret KEY=MCP_ANALYST_TOKEN               # L2: prints "MCP_ANALYST_TOKEN set", no value
# L3: enable the flag in .env.local in place (macOS sed)
sed -i '' 's/^MCP_ANALYST_DECISIONS_ENABLED=.*/MCP_ANALYST_DECISIONS_ENABLED=1/' .env.local
make reset && make up && make health                # L4: exit 0
HEALTH_TIMEOUT_S=5 make health                      # L5: exit 0, runs with 5s (stop soar first to see ~5s failure)
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: localhost' http://localhost:8080/mcp/healthz   # L6: 200
make triage-seed                                    # L7: case 1 TRIAGED
set -a && . ./.env.local && set +a
B=http://localhost:8080
python scripts/mcp_call.py --base $B --path /mcp --token-env MCP_ADAPTER_TOKEN --tool get_alert --args '{"alert_id":"SPL-2026-0001"}'           # L8: exit 0, alert JSON
python scripts/mcp_call.py --base $B --path /mcp-analyst --token-env MCP_ADAPTER_TOKEN --tool record_analyst_decision --args '{"case_id":1,"decision":"approve","analyst_id":"vihag","rationale":"t","chat_ref":"c","instruction_text":"approve","relayed_by":"centurion"}'   # L9: exit 4 (401)
python scripts/mcp_call.py --base $B --path /mcp-analyst --token-env MCP_ANALYST_TOKEN --tool record_analyst_decision --args '{"case_id":1,"decision":"approve","analyst_id":"vihag","rationale":"t","chat_ref":"c","instruction_text":"approve","relayed_by":"centurion"}'  # L10: exit 3, illegal_transition (case is TRIAGED)
python scripts/mcp_call.py --base $B --path /mcp --token-env MCP_ADAPTER_TOKEN --tool record_finding --args '{"case_id":1,"kind":"observation","statement":"RO login on domain admin","evidence_ids":[1],"actor_id":"centurion","confidence":0.9}'   # L11: exit 0 (finding id 1)
python scripts/mcp_call.py --base $B --path /mcp --token-env MCP_ADAPTER_TOKEN --tool propose_disposition --args '{"case_id":1,"action":"escalate","rationale":"see finding","finding_ids":[1],"actor_id":"centurion"}'   # L12: exit 0, DISPOSITION_PROPOSED
python scripts/mcp_call.py --base $B --path /mcp-analyst --token-env MCP_ANALYST_TOKEN --tool record_analyst_decision --args '{"case_id":1,"decision":"approve","analyst_id":"vihag","rationale":"confirmed in chat","chat_ref":"centurion-chat","instruction_text":"Approve case 1","relayed_by":"centurion"}'   # L13: exit 0, APPROVED
curl -s $B/case/v1/cases/1/view | python -c 'import json,sys;v=json.load(sys.stdin);e=[a for a in v["audit"] if a["type"]=="disposition.approved"];print(len(e),e[0]["payload"]["source"]["instruction_text"],v["alert_id"],bool(v["created_at"]))'   # L14: "1 Approve case 1 SPL-2026-0001 True"
make down && make up-public                         # L15: up, guarded
curl -s -o /dev/null -w '%{http_code}\n' https://$NGROK_DOMAIN/case/v1/cases     # L16: 404 (was 200 in C5)
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$NGROK_DOMAIN/mcp       # L17: 401
make down
```
L11/L12 use the C4 tool signatures as of a0134b1 (evidence id 1 is the triage_assessment record from L7). The C5 check expecting `/soar/v1/submissions` 200 was wrong (it needs `case_id`); not repeated here.

## 8. Out of scope
Approving from any UI; changing the agent `/mcp` actor rules; Splunk writes; a Case API reset endpoint; how CENTURION's
connector stores the analyst token (Vihag enters it himself in a secret field; no agent asks for it).
