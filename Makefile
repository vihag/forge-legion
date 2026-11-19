.PHONY: test lint mypy triage-smoke triage-record openapi run-soar run-mcp e2e-soar

PYTHON ?= python3

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

mypy:
	$(PYTHON) -m mypy

openapi:
	$(PYTHON) scripts/export_openapi.py

triage-smoke:
	$(PYTHON) scripts/triage_smoke.py

triage-record:
	$(PYTHON) scripts/triage_smoke.py --record

run-soar:
	$(PYTHON) -m uvicorn services.mock_soar.app:app --host 127.0.0.1 --port 8002

run-mcp:
	$(PYTHON) -m uvicorn services.mcp_adapter.app:app --host 127.0.0.1 --port 8003

e2e-soar:
	CASE_API_URL=http://127.0.0.1:8001 SOAR_CLIENT=http MOCK_SOAR_URL=http://127.0.0.1:8002 \
		$(PYTHON) -m pytest tests/test_soar_integration.py -q