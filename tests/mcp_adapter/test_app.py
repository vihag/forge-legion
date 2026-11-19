"""MCP adapter health route and Host header transport security."""

import pytest
from starlette.testclient import TestClient

from services.mcp_adapter.app import create_app
from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig
from tests.mcp_adapter.test_tools import CONTRACT_TOOLS

INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "qa", "version": "0"},
    },
}

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture(autouse=True)
def _hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MCP_ALLOWED_HOSTS",
        "trimming-breeze-budding.ngrok-free.dev",
    )


def _client() -> TestClient:
    config = AdapterConfig(
        case_api_url="http://127.0.0.1:8000",
        adapter_token=None,
        actor_allowlist=None,
    )
    app = create_app(config=config, case_client=CaseApiClient("http://127.0.0.1:8000"))
    return TestClient(app, base_url="http://127.0.0.1:8003")


def test_ngrok_host_header_accepted_when_configured() -> None:
    with _client() as client:
        response = client.post(
            "/mcp",
            json=INIT_PAYLOAD,
            headers={
                **MCP_HEADERS,
                "Host": "trimming-breeze-budding.ngrok-free.dev",
            },
            follow_redirects=False,
        )
        assert response.status_code == 200


def test_unknown_host_header_rejected() -> None:
    with _client() as client:
        response = client.post(
            "/mcp",
            json=INIT_PAYLOAD,
            headers={**MCP_HEADERS, "Host": "evil.example"},
            follow_redirects=False,
        )
        assert response.status_code >= 400


def test_healthz_route_ok_and_not_a_tool(mcp_session: object) -> None:
    with _client() as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
    tools = mcp_session.list_tools().tools  # type: ignore[union-attr]
    names = {tool.name for tool in tools}
    assert names == set(CONTRACT_TOOLS)
    assert "healthz" not in names