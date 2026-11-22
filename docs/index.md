# Overview

**FORGE** built this repository with Cursor cloud agents against written contracts. **LEGION** is the live agentic SOC: CENTURION and three SENTINEL bots investigate cases through MCP while humans approve consequential actions.

## Three design rules

1. **The Case API owns state** — transitions, policy gates, duplicate guards, and audit events live only in `services/case_api/service.py`. Everything else calls HTTP.
2. **Grok infers; policy routes** — LangGraph calls Grok for assessment, but `config/routing.yaml` picks `autonomous`, `collaborative`, or `review`. The model's `recommended_route` is advisory.
3. **Humans approve anything consequential** — disposition approve/reject requires `actor.type=analyst`. SOAR submit, incident tickets, and auto-resolve policy failures cannot bypass stored approval.

## Component diagram

```mermaid
flowchart LR
  subgraph Source
    Script[Script or random stream]
    Splunk[Mock Splunk]
  end
  Script --> Splunk
  Worker[Triage worker / LangGraph] -->|poll alerts and evidence| Splunk
  Worker -->|assess| Grok[Grok API or fixture]
  Worker -->|ingest, triage, auto-resolve| CaseAPI[Case API + SQLite]
  Worker -->|only if TRIAGED| Hook[Routine webhook]
  Hook --> C[CENTURION intake routine]
  C --> MCP["MCP /mcp"]
  C --> MCPA["MCP /mcp-analyst"]
  S[Evidence / Threat / Disposition SENTINELs] --> MCP
  MCP --> CaseAPI
  MCPA --> CaseAPI
  CaseAPI -->|submit or ticket| SOAR[Mock SOAR]
  Console[LEGION Command Center] --> NG[nginx gateway :8080]
  NG --> CaseAPI
  NG --> Splunk
  NG --> SOAR
  NG --> MCP
```

## End-to-end flow (trigger to SOAR)

One alert, every hop. Solid arrows are code paths; the human is the only actor that can approve.

```mermaid
sequenceDiagram
  autonumber
  participant SPL as Mock Splunk
  participant LG as Triage worker (LangGraph)
  participant GK as Grok API
  participant API as Case API + audit
  participant C as CENTURION
  participant S as SENTINELs (Evidence, Threat, Disposition)
  participant H as Analyst
  participant SOAR as Mock SOAR
  participant LF as Langfuse
  LG->>SPL: poll alerts + evidence (every 5s)
  LG->>API: ingest case (NEW), skip if it already exists
  LG->>LG: deterministic facts (privileged, crown jewel, geo, RDP)
  LG->>GK: POST /chat/completions (strict json_schema)
  GK-->>LG: assessment JSON
  LG->>LG: parse_assessment: schema, MITRE, citations in sent evidence
  LG->>LG: routing.yaml first match: autonomous, collaborative or review
  LG->>API: triage (TRIAGED) + triage_assessment evidence
  alt autonomous and G1 to G10 pass
    LG->>API: auto_resolve (AUTO_RESOLVED), no bot, no human
  else any error or review route
    LG->>API: flag_review (NEEDS_REVIEW)
  else stays TRIAGED
    LG->>C: routine webhook (once per case)
    opt high or critical
      C->>H: ask permission to investigate
      H-->>C: yes
    end
    C->>S: delegate (Evidence and Threat, then Disposition)
    S->>API: findings (INVESTIGATING), proposal (DISPOSITION_PROPOSED)
    C->>H: recommendation card: approve or reject?
    H->>API: decision via /mcp-analyst (APPROVED or REJECTED)
    API->>SOAR: submit, only if APPROVED, once (SUBMITTED)
  end
  opt when tracing enabled (LANGFUSE_ENABLED=1 and keys set)
    LG-.->>LF: root triage trace, node spans, grok.assess, finalize
  end
```

## Stack (in-repo components)

| Component | Role | Package path |
| --- | --- | --- |
| Mock Splunk | Read-only alerts and evidence; reset, replay, stream | `services/mock_splunk/` |
| Triage worker | Polls alerts, runs LangGraph per alert | `services/triage/worker.py` |
| LangGraph triage | Ten-node graph: ingest, evidence, Grok, route, persist | `services/triage/graph.py` |
| Grok client | Live HTTP or fixture client | `services/triage/grok.py` |
| Routing policy | First-match rules from YAML | `config/routing.yaml`, `services/triage/routing.py` |
| Case API + SQLite | System of record for cases and audit | `services/case_api/`, `db/schema.sql` |
| MCP adapter | `/mcp` (11 tools) and `/mcp-analyst` (1 tool) | `services/mcp_adapter/` |
| Routine webhook | POST when status is `TRIAGED` | `services/triage/webhook.py` |
| Mock SOAR | Idempotent SOAR submissions and tickets | `services/mock_soar/` |
| LEGION Command Center | Static console polling metrics | `console/` via nginx `/console/` |
| nginx gateway | Default port 8080: `/case/`, `/splunk/`, `/soar/`, `/mcp`, `/console/` | `deploy/nginx/` |

Public exposure (optional ngrok profile) serves **MCP only** on the public gateway port; Case, Splunk, SOAR, and console stay on the local gateway.

Optional Langfuse tracing for the triage worker is documented in [Observability](observability.md) (`LANGFUSE_ENABLED=1` plus `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` in `.env.local`).