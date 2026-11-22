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

`make env-merge` walks **uncommented** keys in `.env.example` and appends any missing name to `.env.local` with the example value (often empty). Commented lines in `.env.example` — including the `LANGFUSE_*` names — are ignored (`scripts/env_merge.py:_parse_keys`, 13–18; merge loop, 83–89). You must set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` yourself; **`make env-set` refuses `*_KEY`** (`scripts/env_merge.py:_refuse_secret_key`, 26–29).

### How to turn it on

1. Ensure `.env.local` exists (`make env-merge` creates it and prints any newly added key names only).
2. Set non-empty `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` in `.env.local` by hand.
3. `make env-set KEY=LANGFUSE_ENABLED VALUE=1` (prints `LANGFUSE_ENABLED` only).
4. `make up` to recreate containers.

### How to turn it off

1. `make env-set KEY=LANGFUSE_ENABLED VALUE=0`
2. `make up`

Optional: `LANGFUSE_FLUSH_TIMEOUT_S` (default `5`) bounds client shutdown on worker exit (`tracing.py:_flush_timeout`, 149–156; `tracing_langfuse.py:shutdown`, 202–214).

The `tracing` extra (`langfuse` on PyPI) is installed only on the `triage_worker` and `triage` compose build targets via `PIP_EXTRAS: tracing` (`deploy/compose.yml`, build args ~113 and ~135). Only `services/triage/tracing_langfuse.py` imports the Langfuse SDK; other services never load it.

## Prompt management

The triage system message can be stored in Langfuse as a **text** prompt named `triage-system`. The production label selects the version the worker uses at startup.

**Push from the repo:** run `make langfuse-prompt-push` with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` set (names only in script output). The command seeds from `services/triage/prompt.py`. Running it twice should print `pushed vN` then `unchanged vN` when the text already matches.

**Roll back:** in the Langfuse UI, move the `production` label to an older `triage-system` version, then restart the triage worker. The worker caches the resolved prompt for five minutes (`cache_ttl_seconds=300`); after that TTL, or immediately after restart, it picks up the relabelled version.

**Fallback:** when tracing is off, or Langfuse is unreachable, the prompt is empty, or Langfuse returns a fallback client, triage uses the in-repo `SYSTEM_PROMPT` (`prompt_source` metadata `code`, `prompt_version` `none`). One warning is logged per process (reason class only). Triage never fails because of prompt management.