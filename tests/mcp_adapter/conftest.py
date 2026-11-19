"""In-memory MCP session with Case API on ASGI transport."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import anyio
import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from services.case_api.app import create_app as create_case_app
from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig
from services.mcp_adapter.server import build_mcp_server
from tests.mcp_adapter.asgi_transport import FastApiTestTransport


@pytest.fixture
def case_app(tmp_path: Any) -> Iterator[Any]:
    db_path = tmp_path / "cases.db"
    app = create_case_app(db_path=str(db_path))
    yield app
    store = app.state.store
    store.close()


@pytest.fixture
def case_transport(case_app: Any) -> FastApiTestTransport:
    return FastApiTestTransport(case_app)


@pytest.fixture
def adapter_config() -> AdapterConfig:
    return AdapterConfig(
        case_api_url="http://case.test",
        adapter_token=None,
        actor_allowlist=None,
    )


@pytest.fixture
def case_client(case_transport: FastApiTestTransport) -> CaseApiClient:
    return CaseApiClient("http://case.test", transport=case_transport)


@pytest.fixture
def mcp_server(adapter_config: AdapterConfig, case_client: CaseApiClient) -> Any:
    return build_mcp_server(adapter_config, case_client=case_client)


@pytest.fixture
def approved_case_id(case_transport: FastApiTestTransport) -> int:
    transport = case_transport
    client = httpx.Client(transport=transport, base_url="http://case.test")

    def post(path: str, json: dict[str, object]) -> dict[str, object]:
        response = client.post(path, json=json)
        assert response.status_code in (200, 201), response.text
        body: object = response.json()
        assert isinstance(body, dict)
        return body

    case = post(
        "/v1/cases",
        {
            "alert_id": "ALT-MCP",
            "title": "MCP",
            "splunk_severity": "high",
            "summary": "mcp",
            "assigned_analyst": "sam",
            "actor": {"id": "splunk-watcher", "type": "system"},
        },
    )
    case_id = int(case["id"])
    post(
        f"/v1/cases/{case_id}/triage",
        {
            "severity": "high",
            "rationale": "r",
            "model": "grok-3",
            "actor": {"id": "triage-agent", "type": "agent"},
        },
    )
    evidence = post(
        f"/v1/cases/{case_id}/evidence",
        {
            "source": "mock_splunk",
            "ref": "idx=auth",
            "content": {"x": 1},
            "actor": {"id": "evidence-sentinel", "type": "agent"},
        },
    )
    finding = post(
        f"/v1/cases/{case_id}/findings",
        {
            "kind": "observation",
            "statement": "s",
            "evidence_ids": [evidence["id"]],
            "actor": {"id": "finding-agent", "type": "agent"},
        },
    )
    post(
        f"/v1/cases/{case_id}/disposition",
        {
            "action": "contain",
            "rationale": "r",
            "finding_ids": [finding["id"]],
            "actor": {"id": "disposition-sentinel", "type": "agent"},
        },
    )
    post(
        f"/v1/cases/{case_id}/decision",
        {
            "decision": "approve",
            "rationale": "ok",
            "actor": {"id": "analyst-1", "type": "analyst"},
        },
    )
    return case_id


@pytest.fixture
def mcp_session(mcp_server: Any) -> Iterator[Any]:
    async def _run() -> Any:
        async with create_connected_server_and_client_session(mcp_server) as session:
            return session

    # Provide a helper that runs call_tool in anyio
    class SessionRunner:
        def __init__(self, server: Any) -> None:
            self._server = server

        def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            async def _inner() -> Any:
                async with create_connected_server_and_client_session(self._server) as session:
                    return await session.call_tool(name, arguments)

            return anyio.run(_inner)

        def list_tools(self) -> Any:
            async def _inner() -> Any:
                async with create_connected_server_and_client_session(self._server) as session:
                    return await session.list_tools()

            return anyio.run(_inner)

    yield SessionRunner(mcp_server)