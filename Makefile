.PHONY: test lint mypy triage-smoke triage-record openapi run-soar run-mcp e2e-soar up up-public down reset health triage-seed compose-config env-merge env-set gen-secret scenarios-check webhook-check

PYTHON ?= python3
ENV_FILE ?= .env.local
COMPOSE = ENV_FILE=$(ENV_FILE) docker compose --env-file $(ENV_FILE) -f deploy/compose.yml

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
	@test -f $(ENV_FILE) || (echo 'run make env-merge to create $(ENV_FILE)' >&2; exit 1)

env-merge:
	$(PYTHON) scripts/env_merge.py --env-file $(ENV_FILE)

env-set:
	@test -n "$(KEY)" || (echo 'KEY= is required' >&2; exit 1)
	@test -n "$(VALUE)" || (echo 'VALUE= is required' >&2; exit 1)
	$(PYTHON) scripts/env_merge.py --env-file $(ENV_FILE) --set "$(KEY)" --value "$(VALUE)"

gen-secret:
	@test -n "$(KEY)" || (echo 'KEY= is required' >&2; exit 1)
	$(PYTHON) scripts/gen_secret.py $(KEY) --env-file $(ENV_FILE)

up: _check_env
	$(COMPOSE) up -d --build --wait

up-public: _check_env
	@set -a && . ./.env.local && set +a && \
	if [ -z "$$MCP_ADAPTER_TOKEN" ]; then echo 'MCP_ADAPTER_TOKEN is required for up-public' >&2; exit 1; fi && \
	if [ -z "$$NGROK_AUTHTOKEN" ]; then echo 'NGROK_AUTHTOKEN is required for up-public' >&2; exit 1; fi && \
	$(COMPOSE) --profile ngrok up -d --build --wait

down: _check_env
	$(COMPOSE) --profile ngrok --profile tools down

reset: _check_env
	@set -a && . ./$(ENV_FILE) && set +a; \
	$(COMPOSE) stop triage_worker || true; \
	{ \
		echo 'Step 1: case store reset'; \
		$(COMPOSE) exec -T case_api python -m services.case_api.reset && \
		$(COMPOSE) restart case_api && \
		$(COMPOSE) up -d --wait case_api; \
	} || { echo 'reset failed at step 1 (case_api)' >&2; exit 1; }; \
	{ \
		echo 'Step 2: mock soar reset'; \
		curl -sf -X POST "$$PUBLIC_BASE_URL/soar/v1/reset" -H 'content-type: application/json' -d '{}'; \
	} || { echo 'reset failed at step 2 (mock_soar)' >&2; exit 1; }; \
	{ \
		echo 'Step 3: mock splunk reset'; \
		curl -sf -X POST "$$PUBLIC_BASE_URL/splunk/v1/reset" -H 'content-type: application/json' -d '{}'; \
	} || { echo 'reset failed at step 3 (mock_splunk)' >&2; exit 1; }; \
	$(COMPOSE) start triage_worker || true; \
	$(MAKE) health

health: _check_env
	@_hts="$(HEALTH_TIMEOUT_S)"; \
	set -a && . ./$(ENV_FILE) && set +a; \
	[ -n "$$_hts" ] && export HEALTH_TIMEOUT_S="$$_hts"; \
	$(PYTHON) scripts/health.py

triage-seed: _check_env
	@COMPOSE_PROGRESS=quiet $(COMPOSE) --profile tools run --rm -T triage

compose-config:
	$(COMPOSE) config -q

stream-start: _check_env
	@set -a && . ./$(ENV_FILE) && set +a; \
	curl -sf -X POST "$$PUBLIC_BASE_URL/splunk/v1/stream/start" -H 'content-type: application/json' -d '{}'

stream-stop: _check_env
	@set -a && . ./$(ENV_FILE) && set +a; \
	curl -sf -X POST "$$PUBLIC_BASE_URL/splunk/v1/stream/stop" -H 'content-type: application/json' -d '{}'

stream-status: _check_env
	@set -a && . ./$(ENV_FILE) && set +a; \
	curl -sf "$$PUBLIC_BASE_URL/splunk/v1/stream/status"

scenarios-check:
	$(PYTHON) -m services.mock_splunk.scenarios check --world config/world.yaml --dir config/scenarios
	$(PYTHON) -m services.mock_splunk.scenarios check --world tests/fixtures/scenarios/world.yaml --dir tests/fixtures/scenarios

webhook-check: _check_env
	@set -a && . ./$(ENV_FILE) && set +a; \
	url_state=empty; key_state=empty; \
	[ -n "$$ROUTINE_WEBHOOK_URL" ] && url_state=set; \
	[ -n "$$ROUTINE_WEBHOOK_KEY" ] && key_state=set; \
	printf 'ROUTINE_WEBHOOK_URL: %s\n' "$$url_state"; \
	printf 'ROUTINE_WEBHOOK_KEY: %s\n' "$$key_state"