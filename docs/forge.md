# FORGE build loop

**FORGE** is the engineering process that produced this repo: Cursor cloud agents, written contracts, QA verification, and ARCHON merges to `main`. **LEGION** is the runtime SOC demo built on top.

## Contracts C1–C8

| Contract | One-line scope |
| --- | --- |
| **C1** | Case store and Case API (`/v1`) — transitions, audit, SQLite |
| **C2** | Mock Splunk (`/v1`) read-only source plus reset/replay |
| **C3** | LangGraph triage worker and routing; additive Case API fields |
| **C4** | Mock SOAR plus MCP adapter (11 + analyst tools) |
| **C5** | Docker Compose, nginx gateway, ngrok MCP-only public profile, boot-smoke CI |
| **C6** | Analyst decision MCP path, Splunk search tools, CaseView, hardening |
| **C7** | SOC Command Center console and alert stream metrics |
| **C8** | Routine webhook, script timing, HITL rotation, incident ticket, `get_case` include |

Each contract lives under `contracts/C*-contract.md`. Implementation stays within contract boundaries; `/v1` changes are additive only.

## Cursor cloud agent workflow

1. **Task framing** — One cloud agent run per contract, pinned model, single run (no max mode relaunch).
2. **Rules** — `.cursor/rules/architecture.mdc` keeps business logic in the Case API and Mock Splunk as a read-only source.
3. **Skills** — FORGE skills (task framing, QA verify) live in the bot skill library, not in git.
4. **QA verify** — Checks contract acceptance, CI, and spot facts against code before merge.
5. **CI gate** — PR must pass ruff, mypy, scenarios, pytest, OpenAPI drift, boot-smoke (see `trust.md`).
6. **ARCHON** — Merges green PRs; not on the live SOC critical path.

## Cost rules (agent runs)

- Prefer **composer-2.5** for implementation contracts unless the contract names another model.
- At most **one steer** and **one QA retry** per contract (stated in later contracts such as C7/C8).
- Environment blockers over ~10 minutes: stop and document rather than work around in the agent VM.