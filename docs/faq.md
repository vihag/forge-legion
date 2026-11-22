# Interview FAQ

Short answers grounded in `main` at `eaf46fc`. Figures marked **not measured** have no benchmark in repo.

## Problem and demo

**What problem does this solve?**  
Security teams drown in Splunk alerts while risky activity hides in the same queue. The prototype closes low-risk noise with policy-gated auto-resolve and sends the rest to LEGION bots with a human approving disposition and SOAR.

**Why Mock Splunk instead of replacing Splunk?**  
Real enterprises keep detections and evidence in Splunk. This repo treats Splunk as a read-only source (`services/mock_splunk`) so the demo can reset and replay deterministically.

**What are FORGE and LEGION?**  
FORGE built the stack via Cursor cloud agents and contracts C1–C8. LEGION is CENTURION plus three SENTINELs in Grok Bot, calling MCP into the Case API.

**What does the live demo show?**  
Scripted or random alert stream, console metrics, auto-closed benign cases, collaborative cases webhook to CENTURION, investigation, analyst approve/reject, and SOAR ticket. Hero scenario: `config/scenarios/spl-2026-0001-hero.yaml`.

## Architecture

**Where is multi-agent handoff?**  
CENTURION hands off to Evidence and Threat SENTINELs in parallel, then Disposition after findings. Handoffs are visible in Grok Bot chat with priority sends (skills live outside the repo).

**How does CENTURION get cases automatically?**  
Only when status is `TRIAGED` after triage; worker enqueues webhook payload (`handle_triage_result` in `worker.py`). Requires `ROUTINE_WEBHOOK_ENABLED=1` plus URL and key env vars.

**What connects bots to the Case API?**  
MCP: eleven tools on `/mcp`, one `record_analyst_decision` on `/mcp-analyst` with a separate bearer token (`MCP_ADAPTER_TOKEN` vs `MCP_ANALYST_TOKEN`).

**Does Grok Bot memory hold case state?**  
No. SQLite behind the Case API is the system of record; the case Page skill is specified to use API data only. Platform memory usage is **not measured**.

## Policy and routing

**How does routing work in one sentence?**  
Grok assesses every alert; first-match rules in `routing.yaml` pick autonomous, collaborative, or review; auto-close still requires Case API gates G1–G10.

**What are crown jewels?**  
`FIN-DB-01`, `DC-01`, `DC-02`, `JMP-01`, `PAY-APP-02`, `SWIFT-GW-01` — any match forces collaborative routing and fails gate G8.

**Can auto-resolved cases go to SOAR?**  
No. `AUTO_RESOLVED` is terminal; `submit_to_soar` and tickets need a stored analyst approve.

## Human in the loop

**Who can approve?**  
Only requests with `actor.type=analyst` on `POST /v1/cases/{id}/decision`. In demo flow, CENTURION records chat via `/mcp-analyst`.

## Engineering

**How was the code built?**  
One Cursor cloud agent run per contract, QA verify, CI green, ARCHON merge. Architecture rules in `.cursor/rules/architecture.mdc`.

**What is the tech stack?**  
Python 3.11, FastAPI, Pydantic v2, LangGraph, httpx, MCP SDK, SQLite, static console behind nginx; Compose in `deploy/compose.yml`.

**What does CI run?**  
Ruff, mypy, scenario check, pytest (×2), OpenAPI drift, compose boot-smoke — see `trust.md`.

## Security and gaps

**How are secrets handled?**  
Env vars only (see `.env.example` names); never commit `.env.local`. Webhook and MCP logs avoid printing URLs or keys.

**Who can hit SOAR?**  
Case API after approve; Mock SOAR re-checks approval payload. Actor identity is declared by caller — production IAM is **not implemented**.

## Measurements

**Is there a formal auto-close benchmark?**  
**Not measured.** Console `minutes_saved_estimate` uses `MINUTES_PER_ALERT` (default 15) × auto-resolved count.

**Is grok-3-mini benchmarked?**  
**Not measured.** CI uses `GROK_CLIENT=fixture` without live API calls.

**Is the agent's reasoning traceable?**  
Yes, when Langfuse tracing is enabled: one root trace `triage` per alert with a span per LangGraph node, a `grok.assess` generation (prompt user message in, parsed assessment JSON out), and a `finalize` span with route/outcome tags (`services/triage/tracing_langfuse.py`; see [Observability](observability.md)). Scores `assessment_confidence` and `auto_resolve_gate` are written when the case and route qualify (`tracing_langfuse.py:finalize`, 182–198). Off by default; gate requires `LANGFUSE_ENABLED=1` plus the three `LANGFUSE_*` credential names (`tracing.py:_gating_disabled_reason`, 26–39).

**What happens if the tracing service is down?**  
Triage, routing, and Case API writes are identical — tracing is fail-open. SDK errors increment a budget; after five consecutive failures the process stops sending traces for that worker (`tracing_langfuse.py:_rate_limited_error`, 38–42). Worker exit still attempts a bounded flush on a daemon thread (`tracing_langfuse.py:shutdown`, 202–214).

**Is there distributed tracing across MCP and the Case API?**  
**Not implemented** — Langfuse covers the triage worker path only; no OpenTelemetry or LangSmith elsewhere.