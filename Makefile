.PHONY: test lint mypy triage-smoke triage-record openapi run-soar run-mcp e2e-soar up up-public down reset health triage-seed compose-config

PYTHON ?= python3
ENV_FILE ?= .env.local
COMPOSE = docker compose --env-file $(ENV_FILE) -f deploy/compose.yml

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
	CASE_API_URL=http://127.0.0.1:8000 SOAR_CLIENT=http MOCK_SOAR_URL=http://127.0.0.1:8002 \
		$(PYTHON) -m pytest tests/test_soar_integration.py -q

_check_env:
	@test -f .env.local || (echo 'cp .env.example .env.local'; exit 1)

up: _check_env
	$(COMPOSE) up -d --build --wait

up-public: _check_env
	@if [ -z "$$MCP_ADAPTER_TOKEN" ]; then echo 'MCP_ADAPTER_TOKEN is required for up-public' >&2; exit 1; fi
	@if [ -z "$$NGROK_AUTHTOKEN" ]; then echo 'NGROK_AUTHTOKEN is required for up-public' >&2; exit 1; fi
	$(COMPOSE) --profile ngrok up -d --build --wait

down: _check_env
	$(COMPOSE) --profile ngrok --profile tools down

reset: _check_env
	@set -a && . ./.env.local && set +a && \
		echo 'Step 1: case store reset' && \
		$(COMPOSE) exec -T case_api python -m services.case_api.reset || \
		(echo 'reset failed at step 1 (case_api)' >&2; exit 1) && \
		$(COMPOSE) restart case_api && \
		$(COMPOSE) up -d --wait case_api && \
		echo 'Step 2: mock soar reset' && \
		curl -sf -X POST "$$PUBLIC_BASE_URL/soar/v1/reset" -H 'content-type: application/json' -d '{}' || \
		(echo 'reset failed at step 2 (mock_soar)' >&2; exit 1) && \
		echo 'Step 3: mock splunk reset' && \
		curl -sf -X POST "$$PUBLIC_BASE_URL/splunk/v1/reset" -H 'content-type: application/json' -d '{}' || \
		(echo 'reset failed at step 3 (mock_splunk)' >&2; exit 1) && \
		$(MAKE) health

health: _check_env
	@set -a && . ./.env.local && set +a && $(PYTHON) scripts/health.py

triage-seed: _check_env
	$(COMPOSE) --profile tools run --rm triage

compose-config:
	$(COMPOSE) config -q