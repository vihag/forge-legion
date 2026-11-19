# forge-legion

FORGE × LEGION: agentic SOC triage and investigation on legacy Splunk, powered by Grok Bots, LangGraph and the Grok API.

This repository ships the C1 case store and case API (`/v1`), the C2 Mock Splunk service, and the C3 LangGraph triage runner. The binding contracts are [`contracts/C1-contract.md`](contracts/C1-contract.md), [`contracts/C2-contract.md`](contracts/C2-contract.md), and [`contracts/C3-contract.md`](contracts/C3-contract.md).

## Setup

Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

Case API:

```bash
uvicorn services.case_api.app:app --reload
```

The database path is `CASE_DB_PATH`, or `./data/cases.db` when that variable is unset. Liveness is `GET /healthz`. Interactive docs are at `/docs`.

Mock Splunk listens on port 8001:

```bash
uvicorn services.mock_splunk.app:app --port 8001
```

## Reset

Case API, drop and recreate the tables:

```bash
python -m services.case_api.reset
```

Mock Splunk reloads its fixtures and drops replays. State is in memory, so restarting the process does the same thing:

```bash
curl -X POST http://127.0.0.1:8001/v1/reset -H 'content-type: application/json' -d '{}'
```

## Triage (C3)

Run one alert through LangGraph triage (requires Mock Splunk and Case API URLs, or use the smoke script which starts both in-process):

```bash
export CASE_API_URL=http://127.0.0.1:8000
export MOCK_SPLUNK_URL=http://127.0.0.1:8001
export XAI_API_KEY=...  # required for live Grok
python -m services.triage run --alert-id SPL-2026-0001
```

Live smoke (real Grok call, prints `TriageResult` JSON). Exits **2** when `XAI_API_KEY` is unset:

```bash
make triage-smoke
```

Record a fresh Grok fixture after a successful smoke:

```bash
make triage-record
```

CI uses recorded fixtures under `tests/triage/fixtures/` and blocks outbound network.

## Test

```bash
make lint
make mypy
make test
make openapi -- --check
```

`scripts/export_openapi.py` rewrites `contracts/openapi.json` and `contracts/mock_splunk.openapi.json` from the live apps. `--check` exits 1 when either committed file has drifted.

## API

All case routes are under `/v1` and return JSON. Errors use `{error, message, details}`.

| Method and path | Role |
|---|---|
| `POST /v1/cases` | Ingest an alert as a `NEW` case |
| `POST /v1/cases/{case_id}/triage` | Record a triage assessment |
| `POST /v1/cases/{case_id}/evidence` | Attach evidence |
| `POST /v1/cases/{case_id}/findings` | Record an observation or hypothesis |
| `POST /v1/cases/{case_id}/disposition` | Propose a disposition |
| `POST /v1/cases/{case_id}/decision` | Analyst approves or rejects the open disposition |
| `POST /v1/cases/{case_id}/soar-submission` | Submit an approved case to SOAR |
| `GET /v1/cases/{case_id}/view` | Case, evidence, findings, disposition, decision, SOAR, audit |
| `GET /v1/events?after_id=` | Cursor-polled audit stream |

State moves only through `services/case_api/service.py`. The allowed moves are the `TRANSITIONS` dict in `services/case_api/state.py`. C1's SOAR client is `FakeSoarClient`, which returns `SOAR-<case_id>-0001`.

## Mock Splunk

Mock Splunk is a read-only fixture source plus `POST /v1/reset` and `POST /v1/replay`. It does not create cases. A new evidence source is one module, one fixture file, and one registry line, described in `contracts/C2-contract.md` §4.

| Method and path | Role |
|---|---|
| `GET /v1/alerts` | Alert feed, paged by `after_seq` |
| `GET /v1/alerts/{alert_id}` | One alert |
| `GET /v1/alerts/{alert_id}/evidence` | Evidence linked to an alert |
| `GET /v1/evidence` | Evidence in a time window for one source |
| `GET /v1/sources` | Registered sources |
| `POST /v1/reset` | Reload fixtures and drop replays |
| `POST /v1/replay` | Emit a new alert id for an existing alert |

## Known limitations

- The actor `type` is self-declared, so the 403 shows the rule but does not authenticate anyone.
- Rejected attempts are not audited.
- `FakeSoarClient` is synthetic by design: C1 performs no real remediation.
