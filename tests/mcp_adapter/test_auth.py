"""Bearer token gate for streamable HTTP /mcp."""

from starlette.testclient import TestClient

from services.mcp_adapter.app import create_app
from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig

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


def _initialize(
    *,
    token: str | None,
    adapter_token: str | None,
) -> int:
    config = AdapterConfig(
        case_api_url="http://127.0.0.1:8000",
        adapter_token=adapter_token,
        actor_allowlist=None,
    )
    app = create_app(config=config, case_client=CaseApiClient("http://127.0.0.1:8000"))
    headers = dict(MCP_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    with TestClient(app, base_url="http://127.0.0.1:8003") as client:
        response = client.post(
            "/mcp",
            json=INIT_PAYLOAD,
            headers=headers,
            follow_redirects=False,
        )
        return response.status_code


def test_bearer_required_when_token_set() -> None:
    assert _initialize(token="secret-token", adapter_token="secret-token") == 200
    assert _initialize(token="wrong-token", adapter_token="secret-token") == 401
    assert _initialize(token=None, adapter_token="secret-token") == 401


def test_no_token_configured_allows_local() -> None:
    assert _initialize(token=None, adapter_token=None) == 200