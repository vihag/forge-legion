# How trust is enforced

Trust is layered: deterministic code runs before any model call; model output is schema-bound and re-validated; routing and auto-resolve policy do not trust model intent; the Case API is the only writer of case state; human approval uses a separate MCP surface; failures fail closed into `NEEDS_REVIEW`.

## Defense-in-depth layers

```mermaid
flowchart TB
  subgraph L1["L1 — Facts before model"]
    DC[deterministic_checks / compute_facts]
  end
  subgraph L2["L2 — Model output"]
    G[Grok strict json_schema + parse_assessment]
  end
  subgraph L3["L3 — Routing policy"]
    R[decide first-match on routing.yaml]
  end
  subgraph L4["L4 — Auto-resolve gate"]
    P[G1–G10 in CaseService._policy_gate_failed]
  end
  subgraph L5["L5 — State machine"]
    T[TRANSITIONS / check_transition]
  end
  subgraph L6["L6 — Human approval"]
    A[/mcp-analyst record_analyst_decision]
  end
  subgraph L7["L7 — Audit & provenance"]
    E[triage_assessment evidence + SQLite audit]
  end
  subgraph L8["L8 — Fail closed"]
    F[flag_review → NEEDS_REVIEW]
  end
  DC --> G --> R --> P
  R --> T
  A --> T
  T --> E
  G -.->|error| F
  R -.->|route review| F
```

| Layer | Mechanism | Where in code | Failure it stops |
| --- | --- | --- | --- |
| a) Deterministic facts | Privileged account regex, crown-jewel host intersection, geo/RDP signals computed before Grok | `services/triage/checks.py:compute_facts`; node `deterministic_checks` in `services/triage/graph.py` | Model cannot invent “privileged” or crown-jewel flags; facts are in the prompt and stored on triage |
| b) Strict schema + parse | `response_format` `json_schema` (`strict: True`); Pydantic `GrokAssessment`; citation IDs ∈ attached evidence; MITRE `T####(.###)?` | `services/triage/grok.py:HttpGrokClient.assess`, `parse_assessment` | Hallucinated evidence IDs, bad MITRE, malformed JSON → `invalid_model_output` |
| c) Policy routing | First matching rule in `config/routing.yaml`; `decide()` uses facts + assessment + Splunk severity; `recommended_route` not read | `services/triage/routing.py:decide`; `docs/routing.md`; `tests/triage/test_routing.py::test_model_recommended_route_is_advisory` | Model cannot pick autonomous vs collaborative track by itself |
| d) G1–G10 gate | Re-check stored triage fields at auto-resolve; `AUTO_RESOLVE_ENABLED` default off; `AUTO_RESOLVE_MIN_CONFIDENCE` default `0.8`; G10 requires `source=triage_assessment` on this case | `services/case_api/service.py:auto_resolve_case`, `_policy_gate_failed` | Overconfident or high-risk auto-close even if worker requests it |
| e) State machine | `TRANSITIONS` + `check_transition`; illegal op → `IllegalTransition` (409); wrong actor → `Forbidden` (403); 4xx → no audit | `services/case_api/state.py`; `services/case_api/service.py:_require_transition`; `tests/case_api/test_audit.py::test_rejected_call_writes_no_event` | Bots cannot skip states or audit refused writes |
| f) Human approval | Single tool on `/mcp-analyst`; bearer `MCP_ANALYST_TOKEN`; `MCP_ANALYST_ALLOWLIST`; `instruction_text` must contain decision word (checked when `source` is present: always on /mcp-analyst; optional on raw POST /decision); SOAR/ticket need stored approve | `services/mcp_adapter/analyst_server.py:record_analyst_decision`; `services/case_api/service.py:decide`; `submit_to_soar` / `create_incident_ticket` approval checks | SOAR and tickets without analyst path |
| g) Atomic audit + provenance | One SQLite transaction per mutation; assessment blob in evidence `source=triage_assessment` | `services/case_api/service.py` (`_audit` on success paths); `services/triage/graph.py:persist_triage` (`add_evidence`) | Split-brain between status and history |
| h) Fail closed | Any triage error or policy `review` route → `flag_review` + `triage_failure` evidence | `services/triage/graph.py:flag_review`, conditional edges after `grok_assess` / `validate` / `persist_triage` | Silent acceptance of bad model or policy path |

## Hard vs soft guarantees

| Guarantee | Hard (code) | Soft (convention / skill) |
| --- | --- | --- |
| Evidence citations refer to attached Splunk IDs | `parse_assessment` (`services/triage/grok.py:186-189`) | — |
| Route is policy, not model | `decide()` ignores `recommended_route` (`services/triage/routing.py`) | — |
| Auto-resolve | G1–G10 in Case API (`service.py:_policy_gate_failed`) | — |
| Only analyst approves | `body.actor.type != "analyst"` → 403 (`service.py:374-378`) | — |
| Only CENTURION calls analyst MCP | — | Bot lane rules live in external LEGION skills (not in repo; see `docs/bots.md`) |
| `relayed_by` identifies CENTURION | Field required and stored with `mcp:` prefix (`analyst_server.py:78-92`) | Server does **not** verify `relayed_by == centurion` (stated in `contracts/C6-contract.md`; no compare in `analyst_server.py`) |
| MCP credential scope | Separate tokens and allowlist (`services/mcp_adapter/config.py`) | Connectors are account-wide; no per-bot credential in code |
| Analyst identity | `actor.type` and `analyst_id` on request | Not production IAM — self-declared actor (`docs/trust.md`) |
| Bot silence / stall recovery | — | **No 2-minute watchdog in repository code at `main`**; any such rule would be external bot skill only |

## Related

Operational trust notes (metrics, CI, gaps): [Trust](../trust.md).