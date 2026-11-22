# Limitations and roadmap

Known gaps at `main` and sensible next steps. Items already called out in `docs/trust.md` and `docs/faq.md` are consolidated here with code pointers.

## Known gaps

| Area | Today | Evidence |
| --- | --- | --- |
| Splunk / SOAR | In-repo mocks with OpenAPI contracts | `services/mock_splunk/`, `services/mock_soar/`; not production SIEM/SOAR |
| Approvals identity | `actor.type` and `analyst_id` are caller-declared; 403 enforces role rules only | `service.py:decide` (374-378); `docs/trust.md` |
| Per-bot MCP credentials | Account-wide connectors; analyst path uses allowlist + separate token | `MCP_ANALYST_ALLOWLIST`, `MCP_ANALYST_TOKEN` in `services/mcp_adapter/config.py`; `docs/mcp.md` |
| `relayed_by` trust | Stored, not validated against CENTURION | `analyst_server.py:92`; `contracts/C6-contract.md` |
| Tracing | **No LangSmith or OpenTelemetry** in this repository | `docs/trust.md`; no `otel` / `langsmith` imports under `services/` |
| Model choice / quality | Default `grok-3-mini`; **not benchmarked** in CI or docs | `services/triage/config.py:37`; live API not used in pytest (`GROK_CLIENT=fixture`) |
| Webhook durability | In-process `_sent` set per worker process | `services/triage/webhook.py:79-80, 114-116` |
| Rejected mutations audit | 4xx calls write **no** audit event | `tests/case_api/test_audit.py::test_rejected_call_writes_no_event` |
| Per-entity correlation | One case per `alert_id`; no multi-alert merge | `service.py:create_case` duplicate guard; C8 contract defers per-entity gate |
| Scale | Single SQLite file; triage worker concurrency **1 or 2** only | `deploy/compose.yml`; `worker.py:_validate_concurrency` (54-56) |
| Cost / SOC metrics | `cost_usd` null unless price env vars set; `minutes_saved_estimate` is an assumption | `service.py` metrics (~971+); `MINUTES_PER_ALERT` in `.env.example` |
| Bot operational rules | Handoffs, stall handling, lane ownership | External LEGION skills — **not in repo** (`docs/bots.md`) |

## Roadmap (not implemented)

| Priority theme | Next step | Touches |
| --- | --- | --- |
| Production data plane | Replace mocks with real Splunk/SOAR behind same HTTP contracts | `contracts/*.openapi.json`, env base URLs |
| Identity | Bind approve/reject to real IdP; stop trusting self-declared `actor` | Case API auth middleware, MCP token binding |
| Credentials | Per-bot or per-workload MCP tokens instead of account-wide connectors | `services/mcp_adapter`, deploy secrets |
| Observability | Add OpenTelemetry or LangSmith traces for triage and MCP | `services/triage`, `services/mcp_adapter` |
| Model governance | Benchmark `grok-3-mini` (or successors) on fixture replay set; gate model changes | `tests/triage/fixtures/`, CI eval job |
| Webhook reliability | Persistent outbox / dedupe across worker restarts | `webhook.py`, new store table |
| Investigation UX | Per-entity open-case correlation (deferred past C8) | `service.py` ingest, contract C8 addendum |
| Scale-out | Move Case store off single-file SQLite; horizontal workers with leased alerts | `services/case_api/store.py`, `worker.py` poll loop |

## Honesty constraints (docs and demos)

- Do not claim live Grok precision/recall without a measured eval in this repo.
- Do not claim distributed tracing — it is absent today.
- Console “minutes saved” is `auto_resolved × MINUTES_PER_ALERT` (default 15), not measured SOC time (`docs/faq.md`).