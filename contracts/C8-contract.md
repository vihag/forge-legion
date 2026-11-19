# C8 contract: routine webhook, script timing, HITL rotation, incident ticket, get_case include (#30)

Base: `main` after C7 merges (contracted against d67a0bb + C7 + C7-addendum-scenarios as written). Author: FORGE Architecture.
No code here. One composer-2.5 run, at most 1 steer. STOP RULE: an env/CI blocker over 10 min is skipped and noted in the PR.
Out of scope: the per-entity open-case gate (stays on its follow-up issue).

## 0. Reasoning in brief (for the demo)
- **The Case API still decides.** The webhook only reports a status the Case API already set; the worker reads it back
  (`GET /v1/cases/{id}`) and sends only when it is `TRIAGED`. The incident ticket's approval check lives in the Case API, not in MCP.
- **Secrets never surface.** `ROUTINE_WEBHOOK_URL` and `ROUTINE_WEBHOOK_KEY` are treated as secrets: never logged, never
  printed by make targets. Vihag has already set both in `.env.local`; nothing overwrites them.
- **/v1 stays backward compatible.** New endpoints, new optional query params and fields; default `get_case` response unchanged.

## a. Outbound routine webhook (triage worker)
- New module `services/triage/webhook.py` (pure sender, injectable `httpx` transport and clock). Called by the worker after a case
  settles (after the C7 auto-resolve request returns or is skipped). The worker re-reads the case; it enqueues only if
  `status == "TRIAGED"`. `AUTO_RESOLVED`, `NEEDS_REVIEW`, `skipped_existing` and anything else never enqueue.
- Payload (JSON, exactly these keys): `{case_id:int, alert_id:str, severity:<assessed severity>, route:str, rule:str}`.
- Request: `POST $ROUTINE_WEBHOOK_URL`, headers `Authorization: Bearer $ROUTINE_WEBHOOK_KEY` (the env value is the bare key; the sender adds `Bearer `)
  and `Content-Type: application/json`. Timeout 5 s.
- Fire-and-forget: one background thread with a bounded queue (100). Enqueue is non-blocking; a full queue drops the oldest
  item and logs `routine webhook queue full, dropped case_id=N`. Triage throughput is unaffected by a slow or dead endpoint.
- Retry: up to 4 attempts on transport error, 429 or 5xx, backoff 1 s, 2 s, 4 s (injected sleep in tests). Other 4xx: no retry.
- At most one delivery attempt series per case per worker process (in-memory sent set); restarts don't re-send because
  existing cases are `skipped_existing`.
- Env: `ROUTINE_WEBHOOK_ENABLED=0` (default), `ROUTINE_WEBHOOK_URL=`, `ROUTINE_WEBHOOK_KEY=`. Active only when enabled is `1`
  AND both values are non-empty; otherwise one startup line `routine webhook: disabled (enabled=0|url=empty|key=empty)`
  using key names and set/empty only. No `ROUTINE_WEBHOOK_AUTH`, no header-name env.
- **Logging rule:** log lines carry `case_id`, attempt number and HTTP status code only. Exceptions are logged as the exception
  class name, never `str(exc)` (httpx messages embed the URL). The worker sets `httpx` and `httpcore` loggers to WARNING.
- Docs: `.env.example` gains the three keys (empty / 0); README "Routine webhook" paragraph says the key is the bare key, set by hand in `.env.local`.

## b. Script mode: `demo_offset_s`
- New optional scenario field `demo_offset_s`: int ≥ 0, or a list of ints ≥ 0 (each entry fires one alert; `pick` entities are
  re-drawn per firing from the stream seed). A 12-minute script with 36 alerts is 36 offsets in [0, 720].
- Coexistence: `demo_slot: n` stays valid and is normalised to `demo_offset_s: [n*60]` (the C7 meaning). A scenario with both
  fields fails validation **V8** (`make scenarios-check` exit 1, message names the file). Served alerts never carry either field.
- Pure planner `plan_script(scenarios, seed) -> list[(offset_s, scenario_id, occurrence)]`, sorted by offset, then scenario id,
  then occurrence. Script mode fires each at start + offset and stops after the last one. Random mode ignores both fields.

## c. HITL used-list (random mode)
- Pick becomes two-stage: choose the class (routine vs hitl) by total weight, exactly as the C7 mix; within hitl, choose by weight
  among hitl_* scenarios **not yet fired this session**. When all have fired, the list clears and a new cycle starts.
  Weight-0 (script-only) scenarios don't count toward "all".
- Session = since the last `POST /v1/reset`; stop/start keeps the list, reset clears it.
- `generate()` stays pure: the used-list is passed in and returned. `GET /v1/stream/status` adds `hitl_used:int, hitl_total:int`.
- The changed pick sequence is fine; C7's MIX1/MIX2 bands must still pass (update any golden sequences in-run).

## d. Sources (verify only)
- Add an assertion test: Mock Splunk `/v1/sources` and the scenario validator accept exactly
  `auth, vpn, proxy, edr, firewall, dns, email, cloud`. If any is missing, that is a C7 regression: fix it in-run and note it in the PR.

## e. Incident ticket and get_case include
**Mock SOAR** (`openapi/mock_soar.json`, OpenAPI-first):
- `POST /v1/tickets` body `{case_id, alert_id, disposition_id, title, severity, summary, approval: SoarApproval, requested_by: Actor}`
  → `{ticket_id: "INC-<case_id>", case_id, created: bool, recorded_at}`; 201 first time, 200 with `created:false` and the
  original record after that (idempotent per case). `GET /v1/tickets?case_id=` lists. `POST /v1/reset` also clears tickets.
**Case API:**
- `POST /v1/cases/{id}/incident-ticket` body `{actor, summary?: str}` (operation_id `create_incident_ticket`).
  Requires a stored analyst `approve` decision on the current disposition, else 409 `no_analyst_approval` (same lock as C7 SOAR
  submit; AUTO_RESOLVED and REJECTED cases always fail it). Allowed in `APPROVED` and `SUBMITTED`; does **not** change status.
  Actor `system` → 403. Emits `soar.ticket_created` once (only when `created:true`). Mock SOAR down → 502 `soar_unavailable`.
- `GET /v1/cases/{id}?include=evidence,findings,audit&audit_limit=N` (comma list, any subset; unknown value → 422).
  Adds optional keys `evidence`, `findings`, `audit` only when requested (excluded when unset). `audit_limit` default 50,
  range 1–200, returns the most recent N events in chronological order. With no params the response is key-for-key identical to today.
**MCP** (`/mcp`, same tool group and allowlist as `propose_disposition` / `request_soar_action`; not added to `/mcp-analyst`):
- New tool `create_incident_ticket(case_id:int, actor_id:str, summary:str|None=None)`, a thin pass-through.
- `get_case` gains optional `include: list[str] | None` and `audit_limit: int | None`; omitted → today's behaviour.
- `contracts/mcp_tools.json`, `contracts/openapi.json`, `contracts/mock_soar.openapi.json` regenerated (`export_openapi.py --check` green).

## Affected files (expected)
`services/triage/{webhook.py,worker.py}`, `services/mock_splunk/{scenarios.py,stream.py,routes.py}`, `services/mock_soar/{routes,service,store}.py`,
`services/case_api/{routes,service,store,soar}.py`, `contracts/{models,mock_soar,mcp_tools}.py` + JSON exports,
`services/mcp_adapter/{server,case_client}.py`, `db/schema.sql` (no change expected; tickets live in Mock SOAR),
`scripts/env_merge.py`, `Makefile`, `.env.example`, `README.md`, tests below.

## QA checks (falsifiable)
- **WH1** `tests/triage/test_webhook.py`: local stub transport records requests. A TRIAGED case → exactly 1 POST, body keys
  exactly the 5 above, `Authorization` header equals `"Bearer " + key` byte for byte.
- **WH2** an AUTO_RESOLVED case and a `skipped_existing` case → 0 POSTs.
- **WH3** stub returns 503, 503, 200 → 3 attempts, sleeps `[1, 2]`; stub returns 400 → 1 attempt.
- **WH4** non-blocking: stub sleeps 10 s; `enqueue()` returns in < 50 ms and the worker processes the next alert.
- **WH5** disabled cases: `ENABLED=0`, or empty URL, or empty KEY → 0 POSTs and the startup line names only key names.
- **WH6 (no secrets in logs)** URL `http://stub.invalid/hook/URL-CANARY-91c2` and KEY `KEY-CANARY-4d7a`; run success,
  retry, 400 and transport-error paths with caplog at DEBUG (root, httpx, httpcore) and capsys; assert none of
  `URL-CANARY-91c2`, `stub.invalid/hook`, `KEY-CANARY-4d7a` or `Bearer KEY-CANARY-4d7a` appears in any record or stdout/stderr.
- **SB1** `plan_script` on a fixture with 36 offsets over 720 s → 36 items, sorted, last offset ≤ 720; injected clock fires all 36, then stream stops.
- **SB2** `demo_slot: 2` plans at 120 s; a file with both fields fails V8 (exit 1).
- **HL1** seeded (`seed=42` and `seed=7`, N=300, fixtures): split the hitl firings into consecutive windows of `hitl_total`;
  every full window has no repeats. **HL2** after reset, `hitl_used == 0`; stop/start keeps it. MIX1/MIX2 still pass.
- **SR1** sources assertion (d).
- **T1** ticket: approved case → 201 `INC-<id>`, `created:true`, one `soar.ticket_created`; second call → 200, `created:false`, same id, no new event.
- **T2** TRIAGED, AUTO_RESOLVED, REJECTED cases → 409 `no_analyst_approval`; actor `system` → 403; status unchanged after T1.
- **G1** `get_case` with no params equals today's response keys (snapshot); `include=audit&audit_limit=3` → 3 newest, chronological;
  `include=bogus` → 422; `audit_limit=0` and `201` → 422. **G2** MCP `get_case` / `create_incident_ticket` pass through (fake client).
- **ES1** `env-set` refuses `ROUTINE_WEBHOOK_KEY` (via `*_KEY`) and `ROUTINE_WEBHOOK_URL` (exit 2, prints the key name only).
- **EM1** `env-merge` on a file that already has `ROUTINE_WEBHOOK_URL` and `ROUTINE_WEBHOOK_KEY` set leaves both values byte-identical.
- CI: ruff, mypy strict, pytest, `export_openapi.py --check`, compose boot-smoke all green.

## Env and secrets on the laptop
- `make env-merge` adds `ROUTINE_WEBHOOK_ENABLED=0`, `ROUTINE_WEBHOOK_URL=`, `ROUTINE_WEBHOOK_KEY=` only where missing; it never
  overwrites existing values (Vihag has already set URL and KEY). `env-set` keeps refusing `*_TOKEN`, `*_KEY` and is extended to refuse
  `ROUTINE_WEBHOOK_URL`. Nobody echoes, cats, greps or prints the values. New `make webhook-check` prints only
  `ROUTINE_WEBHOOK_URL: set|empty` and `ROUTINE_WEBHOOK_KEY: set|empty`.

## Laptop verification (ARCHON runs, in /Users/vihaggupta/forge-legion; no `cp .env.example`, no secrets printed)
1. `git pull && make env-merge` → output lists only key names it added (URL and KEY already exist, so not added).
2. `make webhook-check` → `ROUTINE_WEBHOOK_URL: set`, `ROUTINE_WEBHOOK_KEY: set`. `make env-set KEY=ROUTINE_WEBHOOK_KEY VALUE=x` → refused, exit 2.
3. `make env-set KEY=ROUTINE_WEBHOOK_ENABLED VALUE=1 && make env-set KEY=AUTO_RESOLVE_ENABLED VALUE=1`
4. `make up && make reset && make health` → all healthy.
5. `make stream-start` and wait 5 min; `make stream-status` shows `hitl_used`/`hitl_total`.
6. `docker compose --env-file .env.local -f deploy/compose.yml logs triage_worker | grep "routine webhook"` → ≥ 1
   `delivered case_id=N status=2xx` line, every case_id is TRIAGED (`curl -s localhost:8080/v1/cases/N | python -c 'import sys,json;print(json.load(sys.stdin)["status"])'`), none AUTO_RESOLVED.
7. Same logs piped to `grep -c -i -E "http(s)?://|authorization|bearer"` → `0`. (Pattern only; no value is printed.)
8. `make stream-stop && make reset`; stream-status shows `hitl_used: 0`.
9. Script check: `curl -s -X POST localhost:8080/v1/stream/start -H 'content-type: application/json' -d '{"mode":"script"}'`, then
    after it stops, `make stream-status` shows `emitted` equal to the planned count printed by `make scenarios-check`.
10. Ticket (same shell setup as C6 L8, `B=http://localhost:8080`): on an APPROVED case N run twice
    `python scripts/mcp_call.py --base $B --path /mcp --token-env MCP_ADAPTER_TOKEN --tool create_incident_ticket --args '{"case_id":N,"actor_id":"disposition-sentinel"}'`
    → exit 0, `INC-N` with `created:true`, then `created:false`; on a TRIAGED case → exit 3, `no_analyst_approval`.
11. `make env-set KEY=ROUTINE_WEBHOOK_ENABLED VALUE=0` at the end unless Vihag says to leave it on.