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