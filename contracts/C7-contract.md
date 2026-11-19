# C7 contract: SOC Command Center + alert stream (#28)

Base: `main` @ d67a0bb. Author: FORGE Architecture. No code here. One composer-2.5 run, at most 1 steer, at most 1 QA retry.

## 0. Reasoning in brief (for the demo)
- **The Case API decides, everything else asks.** The worker *requests* auto-resolve; the Case API re-checks the policy gate
  from stored triage facts. Metrics are computed in the Case API; `/console` only renders them (no arithmetic beyond formatting).
- **Auto-resolve cannot reach SOAR.** AUTO_RESOLVED is terminal and SOAR needs a stored analyst approval.
- **Actor rename (item-3 ruling):** use the existing `type: "system"` with `id: "policy:autonomous-triage"`, not a new
  `policy` enum value. `system` is already non-analyst (decision route returns 403 today) and avoids a new ActorType value
  in every /v1 response model. Supersedes "actor type `policy`" in #28.
- **/v1 stays backward compatible:** new endpoints, new optional fields, one new status value (same precedent as NEEDS_REVIEW in C3).

## 1. The six items (as listed in #28) and their contract

### 1. Alert stream (mock_splunk)
- `fixtures/stream_templates.yaml`: 12 templates, each with a `weight`, a `pool` (`routine` or `hitl`), alert fields,
  evidence records and a fixture Grok response. Templates carry `{user}`/`{host}` placeholders filled from pools:
  - `routine` pool (benign/false_positive, autonomous): ≥ 60 users (`u.0001…`) and ≥ 30 hosts (`WS-…`), no privileged pattern,
    no crown-jewel host. `hitl` pool: privileged users (`adm.`/`da-`/`svc-admin`), crown-jewel and server hosts. The pools are disjoint
    (test-checked), so a future per-user/host open-case gate (C8) cannot push routine alerts into the human queue.
  - **Default mix by weight: 82% routine (auto-resolve), 18% collaborative.** Required outcome for `generate(42, N)`, N ≥ 100,
    fake client, AUTO_RESOLVE_ENABLED=1: 75–85% pass G1–G10 and auto-resolve; 15–20% go collaborative and stay TRIAGED,
    including ≥1 low→high and ≥1 medium→high uplift (asserted by S2 and MIX1). The collaborative weight covers ≥1 medium→high
    uplift, ≥1 low→high uplift, ≥2 privileged-account, ≥1 crown-jewel host and ≥1 Splunk-high template. The hero SPL-2026-0001 template is `hitl`.
- Pure generator `services/mock_splunk/stream.py: generate(seed: int, n: int, start_index: int = 0) -> list[Alert+evidence]`.
  Weighted template pick plus pool draws via `random.Random(seed)`; ids `SPL-STR-000001…`; timestamps from an injected clock.
  The hero appears exactly once in the first 5.
- Endpoints (OpenAPI-first, `openapi/mock_splunk.json`): `POST /v1/stream/start` (idempotent, 200 + status),
  `POST /v1/stream/stop` (idempotent), `GET /v1/stream/status` → `{running, rate_per_min, seed, emitted, next_index}`.
- Env: `STREAM_RATE_PER_MIN=3`, `STREAM_SEED=42`. `POST /v1/reset` stops the stream and sets `emitted=0, next_index=0`.
- Make: `stream-start`, `stream-stop`, `stream-status` (curl via `$(ENV_FILE)`'s PUBLIC_BASE_URL on :8080).

### 2. Continuous triage worker
- `python -m services.triage worker`; compose service `triage_worker` (not in the `tools` profile), depends on case_api + mock_splunk.
- Polls `GET /v1/alerts?after_seq=` every `TRIAGE_WORKER_POLL_S=5`; `TRIAGE_WORKER_CONCURRENCY=1` (allowed 1–2, else exit 2).
- Ingests through the **existing** idempotent path (409 → `GET /v1/cases?alert_id=`); an already-triaged case → `skipped_existing`, no model call.
- `GROK_CLIENT=fixture|http` unchanged; fixture client answers stream alerts from the template's fixture response, `model_returned="fixture"`.
- Records per case (additive `TriageCreate` optional fields, persisted on `cases`): `model_requested`, `route`, `rule`,
  `verdict`, `recommended_action`, `confidence`, `privileged_account`, `crown_jewel_asset`, `prompt_tokens`, `completion_tokens`.
- `GrokAssessment` gains optional `verdict: benign|false_positive|suspicious|malicious` and
  `recommended_action: none|escalate|contain` (optional so the C3 recording still validates; missing → never auto-resolve).
- `CaseCreate` gains optional `alert_time` (the Splunk alert timestamp). Schema: additive columns, applied on startup with
  `ALTER TABLE … ADD COLUMN` when missing (no reset needed); `db/schema.sql` updated for fresh stores.
- `make reset` also restarts `triage_worker` (its cursor starts at 0; idempotency makes the replay free).

### 3. Autonomous auto-resolve (the item-3 ruling)
- New status `AUTO_RESOLVED`; operation `AUTO_RESOLVE` with the single transition **TRIAGED → AUTO_RESOLVED**. Not from NEW.
  Terminal: no operation lists it as a from-state, so triage, add_evidence, record_finding, propose, approve, reject,
  submit_to_soar and flag_review all return 409.
- `POST /v1/cases/{id}/auto-resolve`, body `{policy_id: "autonomous-triage", triage_evidence_id: int, actor}`.
  Actor must be exactly `{type:"system", id:"policy:autonomous-triage"}`, else 403 (analysts included). Never via `/decision`. No action field.
- Gate, re-checked in the Case API from stored fields; any failure → 409 `{"code":"policy_gate_failed","failed":[rule ids]}`, no event:
  `G1` AUTO_RESOLVE_ENABLED=1 (default 0) · `G2` route=autonomous · `G3` verdict ∈ {benign,false_positive} ·
  `G4` recommended_action ∉ {escalate,contain} · `G5` triage_severity ∈ {low,medium} (no uplift to high/critical) ·
  `G6` splunk_severity ∈ {low,medium} · `G7` privileged_account=false · `G8` crown_jewel_asset=false ·
  `G9` confidence ≥ AUTO_RESOLVE_MIN_CONFIDENCE (0.8) · `G10` triage_evidence_id is this case's `triage_assessment` evidence.
- Routing: `config/routing.yaml` adds `crown_jewel_hosts: [FIN-DB-01]` and rule `splunk_high_or_critical_collaborative`
  before `low_medium_autonomous`. Failing cases stay TRIAGED for CENTURION. (Open-case correlation per entity is deferred to C8: no entity column yet.)
- Audit: exactly one `case.auto_resolved` per success, same transaction, payload
  `{verdict, policy_id, triage_evidence_id, route, gate_checks:["G1",…,"G10"]}`.
- SOAR lock: `submit_to_soar` additionally requires a stored decision with `actor.type=analyst` and `decision=approve`,
  else 409 `no_analyst_approval`. AST test: `services/triage` imports nothing from `services.case_api.soar` / `services.mock_soar` and its config has no SOAR URL.

### 4. `GET /v1/metrics` (read-only, `since` optional ISO-8601, filters on `cases.created_at >= since`)
Computed only from the Case API store (`cases`, `audit_events`). Response `Metrics` (OpenAPI-first):
- `counts`: total, by_status, by_route, auto_resolved, collaborative_open (status TRIAGED and route collaborative), needs_review.
- `uplifts`: map `"<splunk>-><triage>"` → n for rank increases (low<medium<high<critical); `downgrades` total.
- `time_to_triage_s`: first `case.triaged` event time − `alert_time` (fallback `created_at`).
- `time_to_resolve_s.ai_only`: `case.auto_resolved` time − `alert_time`; `.ai_plus_human`: first `disposition.approved` time − `alert_time`.
- Each timing block = `{n, median, average}`. **median** = middle value of the sorted list; for even n, the mean of the two
  middle values (`statistics.median`). **average** = arithmetic mean. Seconds, rounded to 1 dp. Empty → `n:0, median:null, average:null`.
  Cases lacking the end event are excluded.
- `tokens`: prompt, completion, total, per_case_average (over cases with usage). `cost_usd` only when both
  `GROK_PRICE_INPUT_PER_MTOK` and `GROK_PRICE_OUTPUT_PER_MTOK` are set (empty by default), else `null`:
  `prompt*in/1e6 + completion*out/1e6`, 4 dp.
- `auto_resolve_enabled`: bool, from the Case API's own AUTO_RESOLVE_ENABLED.
- `minutes_saved_estimate` = auto_resolved × `MINUTES_PER_ALERT` (15); `assumptions: {minutes_per_alert: 15}` returned for display.
- `agents`: distinct audit actors in the last 15 min `{actor_id, type, events, last_seen}`.
- `recent`: last 20 cases by `updated_at` desc `{id, alert_id, title, status, route, splunk_severity, triage_severity, verdict, updated_at}`.

### 5. `/console`
- `console/index.html`, `app.js`, `style.css`; vanilla JS, no build, no CDN. nginx **:8080 block only**: `location /console/`
  (static), `= /console` → 302 `/console/`. The :8081 public block is unchanged (console and `/case/` → 404).
- Polls `/case/v1/metrics` every 5 s. Shows KPIs, timing, minutes-saved with the "15 min/alert" assumption on screen,
  uplifts, live feed, agents strip, and a fixed **SIMULATED SHIFT** badge. When `auto_resolve_enabled` is false, a visible
  **AUTO-RESOLVE OFF** banner shows and the minutes-saved tile reads "auto-resolve off". Dark theme, ≥18 px base font, contrast ≥ 7:1.
- Renders metric values as returned; no recomputation in JS (review check: no `reduce`/sum over cases in `app.js`).

### 6. From #27
- `scripts/mcp_call.py` exits 4 on HTTP 401/403, including when raised inside the SDK task group (`BaseExceptionGroup` unwrapped to `httpx.HTTPStatusError`).
- `tests/conftest.py` autouse fixture `monkeypatch.delenv` for every key named in `.env.example` (parsed, names only), so tests pass with `.env.local` exported.

## 2. Commit order (one PR)
1. `feat(C7): alert stream` (item 1) → 2. `feat(C7): triage worker + auto-resolve policy` (items 2, 3) →
3. `feat(C7): /v1/metrics` (4) → 4. `feat(C7): console` (5) → 5. `fix(C7): #27 items` (6) + README "Demo shift" section.
OpenAPI regenerated in the commit that changes it; CI green at every commit is preferred, at the last one required.

## 3. STOP RULE
If any env or CI issue blocks for more than 10 minutes, skip it, leave a `TODO(C7-STOP)` note, and list it under
"Skipped (stop rule)" in the PR body with the exact error. Do not relaunch; do not run Docker in the VM.

## 4. Env keys (`.env.example`; merged into the existing `.env.local` via `make env-merge` only)
`STREAM_RATE_PER_MIN=3`, `STREAM_SEED=42`, `TRIAGE_WORKER_POLL_S=5`, `TRIAGE_WORKER_CONCURRENCY=1`,
`AUTO_RESOLVE_ENABLED=0`, `AUTO_RESOLVE_MIN_CONFIDENCE=0.8`, `MINUTES_PER_ALERT=15`,
`GROK_PRICE_INPUT_PER_MTOK=`, `GROK_PRICE_OUTPUT_PER_MTOK=`. No `cp .env.example .env.local` anywhere.
AUTO_RESOLVE_ENABLED stays 0 by default. `make env-merge` never overwrites, so the laptop turns it on with the new
`make env-set KEY=AUTO_RESOLVE_ENABLED VALUE=1` (`scripts/env_merge.py --set`, edits `$(ENV_FILE)` in place, prints the key name only;
refuses keys ending in `_TOKEN` or `_KEY`).

## 5. Falsifiable QA checks (in-process; CI does the compose smoke)
- S1 `generate(42, 30)` twice → identical; `generate(7, 30)` differs; hero exactly once in first 5.
- S2 (bands, not counts) `tests/mock_splunk/test_stream_mix_bands.py::test_template_shares_seed42`: for `generate(42, N)` with
  N ∈ {100, 200, 500}, routine-pool alerts ∈ [0.75, 0.85] of N and hitl-pool alerts ∈ [0.15, 0.20] of N, each N containing ≥1
  low→high and ≥1 medium→high uplift template. (Generator-level; MIX1 below proves the same bands end-to-end.)
- S2b routine and hitl pools are disjoint; routine pool has ≥ 60 users and ≥ 30 hosts and none matches a privileged pattern or crown-jewel host.
- **MIX1 `tests/triage/test_stream_mix.py::test_default_mix_ratio_seed42`**, parametrized N ∈ {100, 200}: `generate(42, N)` through ingest, triage
  (fake/fixture client) and the auto-resolve request against an in-process Case API with AUTO_RESOLVE_ENABLED=1. Asserts
  auto-resolved share ∈ [0.75, 0.85], collaborative share ∈ [0.15, 0.20], needs_review ≤ 0.05, and ≥1 medium→high and ≥1 low→high
  uplift among the collaborative cases. MIX2 same run with AUTO_RESOLVE_ENABLED=0: auto-resolved = 0, those cases stay TRIAGED.
- S3 start/start/status → running, one ticker; stop → running=false; reset → running=false, emitted=0. S4 fake clock at 3/min for 10 min → emitted=30.
- W1 same alert seen twice → 1 case, 1 `case.triaged`, model client called once. W2 concurrency=3 → exit 2.
  W3 usage + model_requested/returned persisted and visible in CaseView.
- P1 benign, autonomous, low, conf 0.9 → AUTO_RESOLVED, exactly +1 `case.auto_resolved`, actor `policy:autonomous-triage`.
- P2 one case per gate G1–G10 failing → 409 with that rule id in `failed`, status TRIAGED, 0 events.
- P3 analyst or agent actor on auto-resolve → 403. P4 auto-resolve from NEW/INVESTIGATING → 409.
- P5 every operation on an AUTO_RESOLVED case → 409. P6 submit_to_soar on AUTO_RESOLVED → 409; fake SOAR submit count 0.
- P7 submit_to_soar on an APPROVED case whose decision row is missing → 409 `no_analyst_approval`. P8 AST test from §1.3 passes.
- M1 fixture store (pinned: 5 cases, known timestamps) → exact counts, uplifts, median/average for odd and even n, empty → nulls.
- M1b `auto_resolve_enabled` is false with AUTO_RESOLVE_ENABLED=0 and true with 1.
- M2 prices unset → `cost_usd: null`; set (in=2, out=10, 1M/0.5M tokens) → 7.0. M3 `since` excludes earlier cases. M4 `/v1/metrics` writes nothing (audit count unchanged).
- C1 nginx template test: `/console/` only in the 8080 server block. CI boot-smoke: `nginx:8080/console/` 200 and body contains
  `SIMULATED SHIFT`; `nginx:8081/console/` 404; `nginx:8081/case/v1/metrics` 404; with `GROK_CLIENT=fixture`,
  `STREAM_RATE_PER_MIN=60`, `AUTO_RESOLVE_ENABLED=1`: after 90 s `counts.total ≥ 3` and `counts.auto_resolved ≥ 1`.
- X1 `mcp_call.py` with the agent token on `/mcp-analyst` → exit 4. X2 `env $(cat .env.example | grep -v '^#' | xargs) pytest` → green (CI job step).
- Plus CI: ruff, mypy strict, pytest, `export_openapi.py --check` (case_api, mock_splunk).

## 6. Laptop verification (ARCHON, `/Users/vihaggupta/forge-legion`; no token is printed)
```bash
git pull && git log --oneline -1                     # C7 merge commit
make env-merge                                       # V1: prints added key names only
grep -c '^STREAM_RATE_PER_MIN=3$' .env.local         # V2: 1
make up && make reset && make health                 # V3: exit 0 (reset also restarts triage_worker)
open http://localhost:8080/console/                  # V3b: AUTO-RESOLVE OFF banner visible (default 0)
make env-set KEY=AUTO_RESOLVE_ENABLED VALUE=1 && make up   # V3c: prints the key name only; case_api recreated
make reset && make health                            # V3d: exit 0
curl -s localhost:8080/case/v1/metrics | python -c 'import json,sys;print(json.load(sys.stdin)["counts"]["total"])'   # V4: 0
open http://localhost:8080/console/                  # V5: dark page, SIMULATED SHIFT badge, zeros, no OFF banner
make stream-start && sleep 600 && make stream-stop   # V6: 10 minutes at 3/min
make stream-status                                   # V7: running=false, emitted between 29 and 31
curl -s localhost:8080/case/v1/metrics | python -c 'import json,sys;c=json.load(sys.stdin)["counts"];print(round(c["auto_resolved"]/max(c["total"],1),2))'   # V7b: roughly 0.7-0.9 (n=30 is small; MIX1 is the hard bound)
python scripts/metrics_check.py --base http://localhost:8080   # V8: exit 0
```
`scripts/metrics_check.py` (new, stdlib): pages `GET /case/v1/cases`, fetches each view, recomputes counts, by_status,
uplifts, auto_resolved and the timing medians from case data and audit, compares to `/case/v1/metrics`
(exact for counts, ±0.1 s for timings), checks `GET /soar/v1/submissions?case_id=` is empty for every AUTO_RESOLVED case,
and exits 0 on a full match (1 with a diff otherwise).
V9: the console's Total, Auto-resolved and Uplift tiles equal the V8 printout (ARCHON screenshot attached to the PR).
V10: `curl -s -o /dev/null -w '%{http_code}\n' localhost:8081/console/` from inside compose is covered by CI; on laptop
`make up-public` is not required for C7.

## 7. Out of scope
Per-entity open-case correlation (C8; the disjoint pools above keep it from starving auto-resolve when it lands), reopening AUTO_RESOLVED, auth on `/console`, real Splunk, any Docker run in the VM.
