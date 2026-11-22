# MCP adapter

Two FastMCP servers: **`/mcp`** (agent token) and **`/mcp-analyst`** (analyst token). Each tool maps to one HTTP call; no business logic and no imports from `services.case_api`. Approval is **not** exposed on `/mcp`.

## `/mcp` tools (11)

Schemas: `contracts/mcp_tools.json`, implementation: `services/mcp_adapter/server.py`.

| Tool | Purpose | Key arguments |
| --- | --- | --- |
| `list_cases` | Filtered case list | `status`, `severity`, `alert_id`, `limit`, `offset` |
| `get_case` | One case | `case_id`; optional `include` list, `audit_limit` |
| `get_case_history` | Audit trail | `case_id` |
| `add_evidence` | Attach evidence | `case_id`, `source`, `ref`, `content`, `actor_id` |
| `record_finding` | Record finding | `case_id`, `kind`, `statement`, `evidence_ids`, `actor_id`, optional `confidence` |
| `propose_disposition` | Propose disposition for approval | `case_id`, `action`, `rationale`, `finding_ids`, `actor_id` |
| `request_soar_action` | Submit approved case to SOAR | `case_id`, `actor_id` |
| `create_incident_ticket` | Open SOAR ticket after approval | `case_id`, `actor_id`, optional `summary` |
| `get_alert` | Mock Splunk alert | `alert_id` |
| `list_alert_evidence` | Evidence for alert | `alert_id`, optional `source` |
| `search_evidence` | Time-window Splunk search | `source` (`*` = all), optional `start`, `end`, `entity` (defaults: last 72h) |

Mutating tools validate `actor_id` against pattern and optional allowlist.

## `/mcp-analyst` tool (1)

Schema: `contracts/mcp_analyst_tools.json`.

| Tool | Purpose | Key arguments |
| --- | --- | --- |
| `record_analyst_decision` | Record approve/reject with chat provenance | `case_id`, `decision`, `analyst_id`, `rationale`, `chat_ref`, `instruction_text`, `relayed_by`, optional `message_ref` |

Calls through to `POST /v1/cases/{id}/decision` with analyst actor and provenance payload.

## Exposure

Local nginx gateway (default port 8080) routes `/mcp` and `/mcp-analyst`. The optional public gateway exposes **MCP only**; Case, Splunk, SOAR, and console paths are not served there.