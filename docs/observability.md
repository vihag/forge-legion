# Observability (Langfuse)

FORGE triage can emit optional traces to [Langfuse](https://langfuse.com) (Cloud or self-hosted ≥ v4.59.0). Tracing is **off by default** and never changes triage outcomes, case writes, routing, auto-resolve, or webhooks.

## What is traced

Each processed alert produces one trace named `triage`.

| Span / observation | Name | Content |
|------------------|------|---------|
| Root | `triage` | `alert_id` in metadata; input `{"alert_id": ...}` |
| LangGraph nodes | `fetch_alert`, `ingest_case`, `fetch_evidence`, `attach_evidence`, `deterministic_checks`, `grok_assess`, `validate`, `route`, `persist_triage`, `flag_review` | Input/output summaries: `{"keys": [...], "evidence_count"?}` only (no raw alert or evidence) |
| Generation (under `grok_assess`) | `grok.assess` | Model, prompt user message, parsed assessment JSON, token usage, `response_id` / `request_id` |
| Finalize | `finalize` | Tags `route:<policy>` and `outcome:<result>`; metadata `case_id`, `rule`, models, ids |

Trace-level scores (in Langfuse UI under the trace):

| Score | When | Meaning |
|-------|------|---------|
| `assessment_confidence` | Assessment present | Model confidence 0–1 |
| `auto_resolve_gate` | Route `autonomous` | `1` if gate `pass`, else `0` with rule in comment |

Skipped existing cases still get `triage` + `finalize` (no generation, no scores).

## Enable tracing

1. Add the variable **names** to `.env.local` by hand (`make env-merge` only adds commented names from `.env.example`). **`make env-set` refuses `*_KEY`** — paste `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` yourself.
2. Set non-empty `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` (e.g. `https://cloud.langfuse.com` or your self-hosted URL).
3. Set `LANGFUSE_ENABLED=1`.
4. Recreate the stack: `make up`.

Optional: `LANGFUSE_FLUSH_TIMEOUT_S` (default `5`) bounds client shutdown on worker exit.

The `tracing` optional dependency is installed only in the `triage_worker` and `triage` images. Other services do not import Langfuse.