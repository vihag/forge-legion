# Design decisions

Short ADR-style records. Each entry is scoped to what is enforced in this repository unless noted.

---

## Fixed LangGraph `StateGraph` vs ReAct agent

| | |
| --- | --- |
| **Context** | Triage must be repeatable in CI, bounded in latency, and auditable per alert. |
| **Decision** | A fixed ten-node `StateGraph` in `services/triage/graph.py` (`build_graph`, `run_triage`) — no tool loop inside the model. |
| **Alternatives** | ReAct-style agent with dynamic tool calls would couple inference to side effects and complicate failure modes. |
| **Consequences** | One Grok HTTP call per alert; failures route to `flag_review` deterministically. Graph structure is documented in `docs/triage.md`. |

---

## One Grok call, no in-graph tools

| | |
| --- | --- |
| **Context** | Assessment must be a single structured payload for routing and Case API storage. |
| **Decision** | `grok_assess` calls `GrokClient.assess` once; tools are not exposed to the model in triage. |
| **Alternatives** | Multi-turn or tool-augmented Grok would increase cost and attack surface. |
| **Consequences** | Splunk reads happen in graph nodes via HTTP clients, not via model tools (`services/triage/clients.py`). |

---

## Default model `grok-3-mini`

| | |
| --- | --- |
| **Context** | Need cheap, fast structured classification for demos and stream volume. |
| **Decision** | Default `XAI_MODEL` is `grok-3-mini` (`services/triage/config.py:37`). |
| **Alternatives** | Larger models — higher cost; not selected as default in code. |
| **Consequences** | Trust rests on schema, gates, and policy — not on benchmarked model quality. **`grok-3-mini` is not benchmarked** for precision/recall in this repo (no eval job; CI uses `GROK_CLIENT=fixture`). |

---

## Policy routing over model routing

| | |
| --- | --- |
| **Context** | Model `recommended_route` can disagree with SOC policy. |
| **Decision** | `config/routing.yaml` + `services/triage/routing.py:decide` choose `autonomous`, `collaborative`, or `review`; model field is advisory only. |
| **Alternatives** | Trust model route — rejected in C3 contract and tests. |
| **Consequences** | Changing routing is a config edit, not a prompt change. See `docs/routing.md`. |

---

## Case API as single state owner + SQLite

| | |
| --- | --- |
| **Context** | Multiple bots and services must not fork case state. |
| **Decision** | All transitions and business rules in `services/case_api/service.py`; persistence in SQLite (`db/schema.sql`, store). |
| **Alternatives** | Distributed state or bot-local stores. |
| **Consequences** | Architecture rule: bots call HTTP only (`.cursor/rules/architecture.mdc`). Scaling limits documented under [limitations](limitations-and-roadmap.md). |

---

## MCP as bot tool surface + separate analyst endpoint

| | |
| --- | --- |
| **Context** | Investigation tools should be widely usable; approval must be narrower. |
| **Decision** | Eleven tools on `/mcp` (`contracts/mcp_tools.json`); one `record_analyst_decision` on `/mcp-analyst` (`contracts/mcp_analyst_tools.json`). Adapter does not import `services.case_api` (architecture rule 11). |
| **Alternatives** | Single MCP with approve tool — rejected so approval stays off the agent token path. |
| **Consequences** | `MCP_ADAPTER_TOKEN` vs `MCP_ANALYST_TOKEN` must differ when analyst MCP is enabled (`services/mcp_adapter/config.py`). |

---

## Lead CENTURION + three specialists, least privilege

| | |
| --- | --- |
| **Context** | Demo SOC narrative: intake lead plus Evidence, Threat, Disposition SENTINELs. |
| **Decision** | Role split described in `docs/bots.md`; SOAR submit only after `APPROVED` (enforced in `service.py:submit_to_soar`). |
| **Alternatives** | Single monolithic bot. |
| **Consequences** | Handoff ordering and “only CENTURION on `/mcp-analyst`” are **enforced by convention / external bot skills**, not by code. |

---

## Auto-resolve off by default

| | |
| --- | --- |
| **Context** | Terminal auto-close is high impact. |
| **Decision** | `AUTO_RESOLVE_ENABLED` defaults to off unless env is `1` (`service.py:_policy_gate_failed` G1; `.env.example`). |
| **Alternatives** | On-by-default with opt-out. |
| **Consequences** | Worker may request auto-resolve; Case API may refuse with `409 policy_gate_failed` and leave `TRIAGED` (`tests/case_api/test_auto_resolve.py::test_p2_gate_failures`). |

---

## Contract-first FORGE build (one pinned cheap Cursor run)

| | |
| --- | --- |
| **Context** | FORGE delivered C1–C8 via cloud agents against written contracts. |
| **Decision** | Contracts in `contracts/`; merge after CI; framing described in `docs/forge.md`. |
| **Alternatives** | Ad-hoc coding without contract gates. |
| **Consequences** | Mock Splunk/SOAR implement real HTTP contracts (`contracts/mock_splunk.openapi.json`, `contracts/mock_soar.openapi.json`) — swappable for production endpoints without changing bot tools. |

---

## Manual Langfuse spans vs LangGraph `CallbackHandler`

| | |
| --- | --- |
| **Context** | C10 added optional Langfuse tracing without changing triage behaviour (`contracts/C10-contract.md` §0). |
| **Decision** | Manual `Tracer` spans around each LangGraph node and the raw httpx Grok call, not the LangChain callback handler. |
| **Alternatives** | `CallbackHandler` would serialize full graph state (alert, evidence) on every node and would not attribute the httpx generation with model, usage, and errors (`contracts/C10-contract.md` §0, points 1–2). |
| **Consequences** | Spans carry summaries only (`keys`, optional `evidence_count`; generation input is the prompt user message) — a coarse trace shape suited to Langfuse Hobby limits. Only `tracing_langfuse.py` imports the SDK (`tracing.py:build_tracer`, 174–185). The contract notes that a stand-in client is enough to pin the span shape, with no callback internals (`contracts/C10-contract.md` §0, point 3). |

---

## Splunk/SOAR mocks behind real API contracts

| | |
| --- | --- |
| **Context** | Demos need reset/replay; production needs the same integration shape. |
| **Decision** | `services/mock_splunk` read-only source; `services/mock_soar` idempotent SOAR facade; neither writes Case API state directly. |
| **Alternatives** | In-memory stubs without OpenAPI. |
| **Consequences** | Triage and MCP always use HTTP URLs (`CASE_API_URL`, `MOCK_SPLUNK_URL`, etc. in `.env.example`). |