# Trust and verification

For a layered defense-in-depth view with code citations, see [How trust is enforced](design/trust-model.md).

## Provenance on analyst decisions

`POST /v1/cases/{id}/decision` accepts optional `source` with `chat_ref`, `instruction_text`, `relayed_by`, and `message_ref`. The analyst MCP tool requires these fields. Audit payload on approve/reject includes `source` when present. `instruction_text` must contain the decision word or the API returns `422 instruction_mismatch`.

## Audit trail

Every successful mutation writes one audit row in the same SQLite transaction as the state change. Rejected 4xx calls write nothing. History is available via `GET /v1/cases/{id}/history` and optional `include=audit` on `get_case`.

## `/v1/metrics` fields (computed server-side)

| Section | Contents |
| --- | --- |
| `counts` | `total`, `by_status`, `by_route`, `auto_resolved`, `collaborative_open`, `with_legion_open`, `needs_review` |
| `uplifts` / `downgrades` | Splunk vs triage severity changes |
| `time_to_triage_s` | Median/average seconds alert → triage event |
| `time_to_resolve_s` | `ai_only` (→ auto-resolved) and `ai_plus_human` (→ approve) |
| `tokens` | Sum and per-case average of prompt/completion tokens on cases |
| `cost_usd` | **null** unless both `GROK_PRICE_INPUT_PER_MTOK` and `GROK_PRICE_OUTPUT_PER_MTOK` are set |
| `auto_resolve_enabled` | Mirrors `AUTO_RESOLVE_ENABLED` |
| `minutes_saved_estimate` | `auto_resolved × MINUTES_PER_ALERT` (default 15) — assumption, not measured SOC savings |
| `agents` | Actor activity in last 15 minutes from audit |
| `recent` | Last 20 cases snapshot for console feed |

Console polls this endpoint; it does not recompute KPIs client-side.

## Tests and CI

Current tree: **394** tests collected via pytest on `main` plus this branch.

Pull request workflow (`.github/workflows/ci.yml`):

| Job | Checks |
| --- | --- |
| `ruff` | Lint |
| `mypy` | Strict typing on `services` and `contracts` |
| `scenarios-check` | Scenario YAML fixtures |
| `pytest` | Unit and integration tests (twice: default and `.env.example` only) |
| `openapi` | OpenAPI export drift |
| `boot-smoke` | Compose stack health, seed triage, MCP public port, console, stream/metrics, reset |

Docs PRs also run `.github/workflows/docs.yml` (`mkdocs build --strict`).

## Known gaps (honest)

- **No LangSmith or OpenTelemetry tracing** in this repository.
- **grok-3-mini was not benchmarked** for precision/recall; live Grok quality is not measured in CI (fixture client only).
- **Actor `type` is self-declared** — 403 enforces rules but is not production IAM.
- **MCP connectors are account-wide** — analyst tool access relies on skill policy plus `MCP_ANALYST_ALLOWLIST`, not per-bot credentials.
- **Webhook delivery** uses an in-process sent-set; not durable across worker restarts.
- **Rejected mutating attempts** are not audited.
- **Per-entity case correlation** (multiple alerts → one case) is not implemented (deferred past C8).