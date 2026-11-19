.PHONY: test lint mypy triage-smoke triage-record openapi

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