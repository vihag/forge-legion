# C5 Contract: Compose deploy, one-origin gateway, ngrok profile, boot-smoke CI

Owner: FORGE Architecture. Next: FORGE Implementation, then FORGE QA. Base: `main` at 77c7649. Issue: [#9](https://github.com/vihag/forge-legion/issues/9) (plus #22 item b).
Hard rules (from FORGE Lead):
- Every URL, host and port comes from `.env.local`, and `.env.example` lists every variable.
- Secrets come only from the environment.
- No service contract or OpenAPI spec changes. `contracts/*.openapi.json`, `contracts/openapi.json` and `contracts/mcp_tools.json` stay byte-identical.
- The Cursor VM runs only `docker compose config` and unit tests. Compose boot happens only in CI.

## 0. Design decisions
1. **One image, five services.** `deploy/Dockerfile` (python:3.11-slim, `pip install .`, `COPY` of the repo minus `.dockerignore`) runs `case_api`, `mock_splunk`, `mock_soar` and `mcp` with different commands. `triage` is a **one-shot** service under the `tools` profile. It's a CLI and doesn't sit behind nginx.
2. **Uniform path prefixes on one origin.** Only nginx publishes a port. Services talk to each other directly on the compose network (`http://case_api:8000`), so prefixes affect only external callers.
3. **nginx uses the official image's `templates/*.template` and envsubst**, so upstream hosts and ports come from env, not hand-edited config.
4. **Reset all three stores together:** the case DB, the Mock SOAR DB and Mock Splunk's in-memory seed. That way no alert id or SOAR id can point at a stale case. The Case API has no reset endpoint, and adding one would change its spec, so reset uses the existing CLI through `compose exec` and then restarts `case_api`.
5. **Two additive code changes, neither affecting a spec:**
   - (a) An MCP adapter `GET /healthz` Starlette route (not an MCP tool, so `mcp_tools.json` is unchanged).
   - (b) A triage CLI `GROK_CLIENT=http|fixture` setting. Fixture mode needs no key and reads `GROK_FIXTURE_PATH`. `TriageResult` is unchanged.
   - Plus the #22b fix to `load_allowed_hosts` (§3).

## 1. Gateway routing (`deploy/nginx/default.conf.template`, listening on `${GATEWAY_PORT}` inside the container)
| External path | Upstream | Path sent upstream | Notes |
|---|---|---|---|
| `= /healthz` | nginx itself | – | `return 200 '{"status":"ok"}'` |
| `/case/` | `${CASE_API_UPSTREAM}` | prefix stripped (`/case/v1/cases` becomes `/v1/cases`) | |
| `/splunk/` | `${MOCK_SPLUNK_UPSTREAM}` | prefix stripped | |
| `/soar/` | `${MOCK_SOAR_UPSTREAM}` | prefix stripped | |
| `= /mcp/healthz` | `${MCP_UPSTREAM}` | `/healthz` | exact match wins over `/mcp/` |
| `= /mcp` and `/mcp/` | `${MCP_UPSTREAM}` | **unchanged** (`proxy_pass http://$upstream;` with no URI) | No nginx redirect. The adapter's `_NormalizeMcpPath` serves both forms without a 307. Uses `proxy_http_version 1.1`, `proxy_buffering off`, `proxy_read_timeout 300s`, passes `Host $host` and `Authorization` |
| anything else | – | – | 404 |

Every proxied location sets `Host $host`, `X-Forwarded-For` and `X-Forwarded-Proto`. The host port binding is `${GATEWAY_BIND}:${GATEWAY_PUBLIC_PORT}:${GATEWAY_PORT}`, and the defaults in the example are `127.0.0.1`, `8080` and `8080`.

### Implementation deviation: `proxy_set_header Host $http_host`
The contract table above documents `Host $host`. The shipped nginx template uses `proxy_set_header Host $http_host` on every upstream proxy location. MCP's transport security checks match the client `Host` header including the port (e.g. `localhost:8080`). Nginx's `$host` normalizes away the port, which made gateway health's MCP `initialize` check fail with HTTP 421 (invalid host). `$http_host` passes the client's `Host` through unchanged so DNS rebinding protection accepts gateway traffic.

## 2. Compose (`deploy/compose.yml`)
- Every service uses `env_file: ../${ENV_FILE:-.env.local}`. Interpolation comes from `--env-file` (default `.env.local`), and `make` always passes it.
- `case_api`, `mock_splunk`, `mock_soar` and `mcp` run `uvicorn <module>:app --host 0.0.0.0 --port ${<SVC>_PORT}`, with a healthcheck that runs `python -c` urllib against the service's own `/healthz` (interval 3s, retries 20).
- `nginx` has `depends_on` all four with `condition: service_healthy`.
- `mcp` gets `MCP_ALLOWED_HOSTS: "${NGROK_DOMAIN:-},${MCP_EXTRA_ALLOWED_HOSTS:-}"`, so the ngrok host is set once, in `NGROK_DOMAIN`.
- `triage` is under `profiles: [tools]`, with `depends_on` `case_api` and `mock_splunk` healthy and `command: python -m services.triage run --alert-id ${SEED_ALERT_ID}`.
- `ngrok` is under `profiles: [ngrok]`, image `ngrok/ngrok:3` (pinned minor), `command: http --url=${NGROK_DOMAIN} nginx:${GATEWAY_PORT}`, with `NGROK_AUTHTOKEN` from env and `depends_on` nginx.
- The named volume `forge_data` is mounted at `/data`, where `CASE_DB_PATH=/data/cases.db` and `SOAR_DB_PATH=/data/soar.db` live.

## 3. MCP_ALLOWED_HOSTS is additive (#22 item b), in `services/mcp_adapter/config.py`
`load_allowed_hosts()` returns `defaults + extras`, deduplicated with order kept. The defaults are `127.0.0.1:*`, `localhost:*` and `[::1]:*`. The extras come from comma-separated `MCP_ALLOWED_HOSTS`, with blanks skipped. A bare host without a port, e.g. `trimming-breeze-budding.ngrok-free.dev`, adds both `host` and `host:*`. Setting the variable can never remove a default.

## 4. Environment (`.env.example`, committed, with every variable listed)
```
ENV_FILE=.env.local
GATEWAY_BIND=127.0.0.1
GATEWAY_PUBLIC_PORT=8080
GATEWAY_PORT=8080
PUBLIC_BASE_URL=http://localhost:8080
CASE_API_PORT=8000
MOCK_SPLUNK_PORT=8001
MOCK_SOAR_PORT=8002
MCP_PORT=8003
CASE_API_UPSTREAM=case_api:8000
MOCK_SPLUNK_UPSTREAM=mock_splunk:8001
MOCK_SOAR_UPSTREAM=mock_soar:8002
MCP_UPSTREAM=mcp:8003
CASE_API_URL=http://case_api:8000
MOCK_SPLUNK_URL=http://mock_splunk:8001
ROUTING_CONFIG=config/routing.yaml
MOCK_SOAR_URL=http://mock_soar:8002
SOAR_CLIENT=http
CASE_DB_PATH=/data/cases.db
SOAR_DB_PATH=/data/soar.db
SEED_ALERT_ID=SPL-2026-0001
GROK_CLIENT=http
GROK_FIXTURE_PATH=/app/tests/triage/fixtures/grok_seed_ok.json
XAI_MODEL=grok-3-mini
XAI_BASE_URL=https://api.x.ai/v1
XAI_API_KEY=
MCP_ADAPTER_TOKEN=
MCP_ACTOR_ALLOWLIST=
MCP_EXTRA_ALLOWED_HOSTS=
NGROK_DOMAIN=trimming-breeze-budding.ngrok-free.dev
NGROK_AUTHTOKEN=
HEALTH_TIMEOUT_S=60
```
- Secrets (`XAI_API_KEY`, `MCP_ADAPTER_TOKEN`, `NGROK_AUTHTOKEN`) are always empty in the example.
- **.gitignore fix:** the current `.env.*` pattern also ignores `.env.example`. Add `!.env.example` and keep `.env.local` ignored.
- `.dockerignore` excludes `.env*`, `data/`, `.git` and the caches.

## 5. Makefile targets (`COMPOSE = ENV_FILE=$(ENV_FILE) docker compose --env-file $(ENV_FILE) -f deploy/compose.yml`)
Every compose target first checks that `$(ENV_FILE)` exists (default `.env.local`), and if not exits 1 with guidance to run `make env-merge`.

| Target | Does | Exit |
|---|---|---|
| `up` | `$(COMPOSE) up -d --build --wait` | non-zero if any healthcheck fails |
| `up-public` | refuses unless `MCP_ADAPTER_TOKEN` and `NGROK_AUTHTOKEN` are non-empty, then `$(COMPOSE) --profile ngrok up -d --build --wait` | 1 on a missing secret |
| `down` | `$(COMPOSE) --profile ngrok --profile tools down` (the volume is kept) | |
| `reset` | (1) `$(COMPOSE) exec -T case_api python -m services.case_api.reset`, then `restart case_api` and wait until healthy; (2) `POST $(PUBLIC_BASE_URL)/soar/v1/reset`; (3) `POST $(PUBLIC_BASE_URL)/splunk/v1/reset`; (4) `make health` | non-zero on the first failed step, and prints which one |
| `health` | `python scripts/health.py` (stdlib only) | 0 only if all checks pass |
| `triage-seed` | `$(COMPOSE) --profile tools run --rm triage` | the CLI's exit code |
| `compose-config` | `$(COMPOSE) config -q` using `.env.example` when `.env.local` is absent (`ENV_FILE ?= .env.local`) | the safe check for the Cursor VM |

`scripts/health.py` reads `PUBLIC_BASE_URL`, `HEALTH_TIMEOUT_S` and, if set, `MCP_ADAPTER_TOKEN`. It checks, retrying until the timeout:
- `/healthz`, `/case/healthz`, `/splunk/healthz`, `/soar/healthz` and `/mcp/healthz` each return 200 with `{"status":"ok"}`;
- an MCP `initialize` JSON-RPC POST to `/mcp` returns 200, with **no 3xx**.

It prints one line per check (`PASS` or `FAIL name code`) and exits 1 if any check fails. The core function `run_checks(base_url, fetch)` takes an injected `fetch` so unit tests can drive it.

## 6. CI job `boot-smoke` (added to `.github/workflows/ci.yml`; existing jobs unchanged)
1. `cp .env.example .env.local`, then append `GROK_CLIENT=fixture`. There's no `XAI_API_KEY` and no ngrok profile.
2. `make up`, then `make health`.
3. `make triage-seed | tee triage.json`. Assert `outcome == "triaged"` (`python scripts/assert_seed_triaged.py triage.json`). That script also GETs `/case/v1/cases?alert_id=SPL-2026-0001` and asserts `total == 1`, `status == "TRIAGED"`.
4. Negative check: `$(COMPOSE) stop mock_soar`, then `! HEALTH_TIMEOUT_S=5 make health` must fail, and its output must include `FAIL soar`. Then `start mock_soar`, and `make health` passes again.
5. `make reset`, then assert `/case/v1/cases` has `total == 0` and `/soar/v1/submissions` has `items == []`.
6. `if: always()`: `$(COMPOSE) logs --no-color > compose.log` (uploaded as an artifact on failure), then `make down`.

## 7. File layout (new, or changed with ~)
```
deploy/Dockerfile
deploy/compose.yml
deploy/nginx/default.conf.template
.dockerignore
.env.example
~.gitignore                        # + !.env.example
~Makefile                          # §5 targets (existing targets unchanged)
scripts/health.py
scripts/assert_seed_triaged.py
~services/mcp_adapter/config.py    # §3 additive hosts
~services/mcp_adapter/app.py       # + Route("/healthz") outside the /mcp mount
~services/triage/__main__.py       # GROK_CLIENT=fixture → FixtureGrokClient; stderr "grok_client=fixture"
~services/triage/config.py         # + grok_client, grok_fixture_path
~.github/workflows/ci.yml          # + boot-smoke job
~README.md                         # local up/reset/health, public mode, the fixture-mode caveat
tests/deploy/__init__.py
tests/deploy/test_static_config.py
tests/deploy/test_health_script.py
~tests/mcp_adapter/test_config.py  # or new file
~tests/triage/test_cli.py          # or new file
```

## 8. Named pytest cases (they run in the Cursor VM, with no Docker needed)
- `tests/mcp_adapter/test_config.py::test_allowed_hosts_defaults_when_unset`
- `::test_allowed_hosts_extras_are_added_not_replacing` (setting `a.example` keeps all 3 defaults)
- `::test_bare_host_adds_host_and_wildcard_port`
- `::test_blank_entries_ignored_and_deduped`
- `tests/mcp_adapter/test_app.py::test_ngrok_host_header_accepted_when_configured`
- `::test_unknown_host_header_rejected`
- `::test_healthz_route_ok_and_not_a_tool` (`tools/list` is unchanged)
- `tests/triage/test_cli.py::test_fixture_mode_runs_without_api_key` (in-process apps via httpx transport)
- `::test_fixture_mode_missing_path_exits_2`
- `::test_http_mode_still_requires_api_key`
- `tests/deploy/test_health_script.py::test_all_ok_exits_0`
- `::test_one_service_down_exits_1_and_names_it`
- `::test_mcp_redirect_counts_as_failure` (a fake 307 response counts as FAIL)
- `::test_sends_bearer_when_token_set`
- `tests/deploy/test_static_config.py::test_every_compose_var_is_in_env_example` (parses `${VAR}` from compose.yml, the nginx template and the Makefile)
- `::test_no_literal_urls_or_hosts_in_compose_or_nginx` (outside `${...}`: no `http://`, `https://`, IP literals, `localhost` or `ngrok-free.dev`)
- `::test_env_example_secrets_are_empty`
- `::test_gitignore_ignores_env_local_not_example` (`git check-ignore`)
- `::test_nginx_routes_cover_all_prefixes` (`/healthz`, `/case/`, `/splunk/`, `/soar/`, `= /mcp`, `/mcp/`, `= /mcp/healthz`)
- `::test_mcp_location_passes_path_unchanged` (`proxy_pass` has no URI part)
- `::test_ngrok_service_only_in_ngrok_profile_and_uses_env_domain`
- `tests/test_openapi_drift.py` passes unchanged (proves there are no spec changes)

## 9. QA acceptance checks (falsifiable)
**A. Cursor VM (no compose boot)**
1. `make compose-config ENV_FILE=.env.example` and `docker compose --env-file .env.example -f deploy/compose.yml --profile ngrok --profile tools config -q` both exit 0. If Docker isn't present in the VM, the static tests in §8 are the substitute, and the report must say so.
2. `pytest -q`, `ruff check .`, `mypy` and `python scripts/export_openapi.py --check` all exit 0.
3. `git diff 77c7649 -- contracts/` is empty except `contracts/C5-contract.md`.

**B. CI boot-smoke (and the same on the laptop)**
4. `make up && make health` exits 0, with all 6 lines PASS.
5. `curl -s -o /dev/null -w '%{http_code}' localhost:8080/case/v1/cases` gives 200. `/splunk/v1/alerts/SPL-2026-0001` gives 200. `/soar/v1/submissions` gives 200. `/nope` gives 404.
6. An MCP initialize POST to `localhost:8080/mcp` and to `/mcp/` both give 200, and `curl -w '%{redirect_url}'` is empty (**no 307**).
7. The same POST with `-H 'Host: evil.example'` gives a non-2xx. With `-H 'Host: trimming-breeze-budding.ngrok-free.dev'` it gives 200, and `localhost` still gives 200 (additive).
8. **Health fails when a service is down:** `docker compose ... stop mock_soar && HEALTH_TIMEOUT_S=5 make health; echo $?` prints a `FAIL soar` line and exits non-zero. Repeat with `stop mcp` and expect `FAIL mcp` plus the initialize check failing. After `start`, it's back to 0.
9. `make triage-seed` exits 0 with `outcome: "triaged"`, and `/case/v1/cases?alert_id=SPL-2026-0001` shows `total 1`, `status TRIAGED`, with no `XAI_API_KEY` set.
10. Run `make triage-seed` again and it gives `outcome: "skipped_existing"` (C3 idempotency through compose).
11. `make reset` exits 0. Afterwards cases `total 0`, SOAR `items []`, and Mock Splunk `GET /splunk/v1/alerts` is back to the seed (no `-R` replays).
12. `MCP_ADAPTER_TOKEN= make up-public` exits 1 before starting anything.

**C. Laptop only (manual, needs real secrets)**
13. `make up-public` with tokens set. `https://trimming-breeze-budding.ngrok-free.dev/mcp` initialize with a bearer gives 200, and without the bearer gives 401.

## 10. Out of scope, limits and budget
- Out of scope: connecting Grok Bot to the tunnel (ARCHON's step), TLS beyond what ngrok provides, production hardening, and a Case API reset endpoint.
- Limit: fixture-mode triage proves the plumbing, not inference. Real-Grok proof is still `make triage-smoke` on the laptop.
- Budget: about 10 new files plus small edits. If the run is tight, cut `up-public`'s guard test and check 10 first.
