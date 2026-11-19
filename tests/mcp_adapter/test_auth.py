"""Bearer token gate for streamable HTTP /mcp."""

from starlette.testclient import TestClient

from services.mcp_adapter.app import create_app
from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig


def test_bearer_required_when_token_set() -> None:
    config = AdapterConfig(
        case_api_url="http://127.0.0.1:8001",
        adapter_token="secret-token",
        actor_allowlist=None,
    )
    app = create_app(config=config, case_client=CaseApiClient("http://127.0.0.1:8001"))
    with TestClient(app) as client:
        denied = client.post("/mcp", json={})
        assert denied.status_code == 401
        allowed = client.post(
            "/mcp",
            json={},
            headers={"Authorization": "Bearer secret-token"},
        )
        assert allowed.status_code != 401


def test_no_token_configured_allows_local() -> None:
    config = AdapterConfig(
        case_api_url="http://127.0.0.1:8001",
        adapter_token=None,
        actor_allowlist=None,
    )
    app = create_app(config=config, case_client=CaseApiClient("http://127.0.0.1:8001"))
    with TestClient(app) as client:
        response = client.post("/mcp", json={})
        assert response.status_code != 401