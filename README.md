# forge-legion

FORGE × LEGION: agentic SOC triage and investigation on legacy Splunk, powered by Grok Bots, LangGraph and the Grok API.

This repository currently ships the C1 case store and case API (`/v1`). The binding contract is [`contracts/C1-contract.md`](contracts/C1-contract.md).

## Setup

Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
uvicorn services.case_api.app:app --reload
```

The database path is `CASE_DB_PATH`, or `./data/cases.db` when that variable is unset. Liveness is `GET /healthz`. Interactive docs are at `/docs`.

## Reset

Drop and recreate the tables:

```bash
python -m services.case_api.reset
```

## Test

```bash
ruff check .
mypy
pytest
python scripts/export_openapi.py --check
```

`scripts/export_openapi.py` rewrites `contracts/openapi.json` from the live app. `--check` exits 1 when the committed file has drifted.

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

## Known limitations

- The actor `type` is self-declared, so the 403 shows the rule but does not authenticate anyone.
- Rejected attempts are not audited.
- `FakeSoarClient` is synthetic by design: C1 performs no real remediation.
