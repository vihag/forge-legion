"""Analyst MCP endpoint tests (C6 §6.1 A1–A9)."""

from __future__ import annotations

import anyio
import httpx
import pytest
from httpx import MockTransport
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

from services.case_api.app import create_app as create_case_app
from services.mcp_adapter.analyst_server import build_analyst_mcp_server
from services.mcp_adapter.app import create_app
from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig, load_config
from services.mcp_adapter.server import build_mcp_server
from tests.mcp_adapter.asgi_transport import FastApiTestTransport

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "qa", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _session_runner(server: object) -> object:
    class SessionRunner:
        def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            async def _inner() -> object:
                async with create_connected_server_and_client_session(server) as session:  # type: ignore[arg-type]
                    return await session.call_tool(name, arguments)

            return anyio.run(_inner)

        def list_tools(self) -> object:
            async def _inner() -> object:
                async with create_connected_server_and_client_session(server) as session:  # type: ignore[arg-type]
                    return await session.list_tools()

            return anyio.run(_inner)

    return SessionRunner()


def _proposed_case(transport: FastApiTestTransport) -> int:
    client = httpx.Client(transport=transport, base_url="http://case.test")
    case = client.post(
        "/v1/cases",
        json={
            "alert_id": "ALT-ANALYST",
            "title": "t",
            "splunk_severity": "high",
            "summary": "s",
            "assigned_analyst": "sam",
            "actor": {"id": "splunk-watcher", "type": "system"},
        },
    ).json()
    case_id = int(case["id"])
    client.post(
        f"/v1/cases/{case_id}/triage",
        json={
            "severity": "high",
            "rationale": "r",
            "model": "grok-3",
            "actor": {"id": "triage-agent", "type": "agent"},
        },
    )
    evidence = client.post(
        f"/v1/cases/{case_id}/evidence",
        json={
            "source": "mock_splunk",
            "ref": "r",
            "content": {"x": 1},
            "actor": {"id": "evidence-sentinel", "type": "agent"},
        },
    ).json()
    finding = client.post(
        f"/v1/cases/{case_id}/findings",
        json={
            "kind": "observation",
            "statement": "s",
            "evidence_ids": [evidence["id"]],
            "actor": {"id": "finding-agent", "type": "agent"},
        },
    ).json()
    client.post(
        f"/v1/cases/{case_id}/disposition",
        json={
            "action": "contain",
            "rationale": "r",
            "finding_ids": [finding["id"]],
            "actor": {"id": "disposition-sentinel", "type": "agent"},
        },
    )
    return case_id


@pytest.fixture
def case_stack(tmp_path: object) -> tuple[FastApiTestTransport, CaseApiClient]:
    app = create_case_app(db_path=str(tmp_path / "cases.db"))  # type: ignore[operator]
    transport = FastApiTestTransport(app)
    return transport, CaseApiClient("http://case.test", transport=transport)


def test_a1_disabled_analyst_endpoint_404_and_no_decision_tool_on_agent() -> None:
    cfg = AdapterConfig(analyst_decisions_enabled=False)
    app = create_app(config=cfg, case_client=CaseApiClient("http://127.0.0.1:8000"))
    with TestClient(app) as client:
        assert client.post("/mcp-analyst", json=INIT, headers=MCP_HEADERS).status_code == 404
    agent = build_mcp_server(cfg)
    tools = _session_runner(agent).list_tools().tools  # type: ignore[union-attr]
    names = {t.name for t in tools}
    assert "record_analyst_decision" not in names


def test_a2_enabled_empty_analyst_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ANALYST_DECISIONS_ENABLED", "1")
    monkeypatch.delenv("MCP_ANALYST_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        load_config()


def test_a2_enabled_duplicate_tokens_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ANALYST_DECISIONS_ENABLED", "1")
    monkeypatch.setenv("MCP_ADAPTER_TOKEN", "same")
    monkeypatch.setenv("MCP_ANALYST_TOKEN", "same")
    with pytest.raises(SystemExit):
        load_config()


def test_a3_agent_token_on_analyst_401_no_case_calls(
    case_stack: tuple[FastApiTestTransport, CaseApiClient],
) -> None:
    transport, _ = case_stack
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    cfg = AdapterConfig(
        adapter_token="agent-token",
        analyst_decisions_enabled=True,
        analyst_token="analyst-token",
    )
    app = create_app(
        config=cfg,
        case_client=CaseApiClient("http://case.test", transport=MockTransport(handler)),
    )
    headers = {**MCP_HEADERS, "Authorization": "Bearer agent-token"}
    with TestClient(app) as client:
        assert client.post("/mcp-analyst", json=INIT, headers=headers).status_code == 401
    assert calls == []


def test_a4_blank_chat_ref_source_required(
    case_stack: tuple[FastApiTestTransport, CaseApiClient],
) -> None:
    _, case_client = case_stack
    cfg = AdapterConfig(analyst_decisions_enabled=True, analyst_token="analyst-token")
    server = build_analyst_mcp_server(cfg, case_client=case_client)
    result = _session_runner(server).call_tool(
        "record_analyst_decision",
        {
            "case_id": 1,
            "decision": "approve",
            "analyst_id": "vihag",
            "rationale": "t",
            "chat_ref": "  ",
            "instruction_text": "approve",
            "relayed_by": "centurion",
        },
    )
    assert result.isError  # type: ignore[union-attr]
    import json

    payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert payload["error"] == "source_required"


def test_a5_analyst_not_allowed(case_stack: tuple[FastApiTestTransport, CaseApiClient]) -> None:
    transport, case_client = case_stack
    calls: list[str] = []

    def counting(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        forward = httpx.Client(transport=transport, base_url="http://case.test")
        return forward.send(request)

    cfg = AdapterConfig(analyst_decisions_enabled=True, analyst_token="analyst-token")
    server = build_analyst_mcp_server(
        cfg, case_client=CaseApiClient("http://case.test", transport=MockTransport(counting))
    )
    result = _session_runner(server).call_tool(
        "record_analyst_decision",
        {
            "case_id": 1,
            "decision": "approve",
            "analyst_id": "mallory",
            "rationale": "t",
            "chat_ref": "c",
            "instruction_text": "approve",
            "relayed_by": "centurion",
        },
    )
    assert result.isError  # type: ignore[union-attr]
    assert calls == []


def test_a6_triaged_case_illegal_transition(
    case_stack: tuple[FastApiTestTransport, CaseApiClient],
) -> None:
    transport, case_client = case_stack
    client = httpx.Client(transport=transport, base_url="http://case.test")
    case = client.post(
        "/v1/cases",
        json={
            "alert_id": "ALT-TRIAGED",
            "title": "t",
            "splunk_severity": "high",
            "summary": "s",
            "assigned_analyst": "sam",
            "actor": {"id": "splunk-watcher", "type": "system"},
        },
    ).json()
    case_id = int(case["id"])
    client.post(
        f"/v1/cases/{case_id}/triage",
        json={
            "severity": "high",
            "rationale": "r",
            "model": "grok-3",
            "actor": {"id": "triage-agent", "type": "agent"},
        },
    )
    before = client.get(f"/v1/cases/{case_id}/history").json()
    cfg = AdapterConfig(analyst_decisions_enabled=True, analyst_token="analyst-token")
    server = build_analyst_mcp_server(cfg, case_client=case_client)
    result = _session_runner(server).call_tool(
        "record_analyst_decision",
        {
            "case_id": case_id,
            "decision": "approve",
            "analyst_id": "vihag",
            "rationale": "t",
            "chat_ref": "c",
            "instruction_text": "approve",
            "relayed_by": "centurion",
        },
    )
    assert result.isError  # type: ignore[union-attr]
    after = client.get(f"/v1/cases/{case_id}/history").json()
    assert len(after) == len(before)


def test_a7_happy_approve_from_proposed(
    case_stack: tuple[FastApiTestTransport, CaseApiClient],
) -> None:
    transport, case_client = case_stack
    case_id = _proposed_case(transport)
    client = httpx.Client(transport=transport, base_url="http://case.test")
    before = client.get(f"/v1/cases/{case_id}/history").json()
    cfg = AdapterConfig(analyst_decisions_enabled=True, analyst_token="analyst-token")
    server = build_analyst_mcp_server(cfg, case_client=case_client)
    result = _session_runner(server).call_tool(
        "record_analyst_decision",
        {
            "case_id": case_id,
            "decision": "approve",
            "analyst_id": "vihag",
            "rationale": "confirmed in chat",
            "chat_ref": "centurion-chat",
            "instruction_text": "Approve case 1",
            "relayed_by": "centurion",
        },
    )
    assert not result.isError  # type: ignore[union-attr]
    view = client.get(f"/v1/cases/{case_id}/view").json()
    assert view["status"] == "APPROVED"
    after = client.get(f"/v1/cases/{case_id}/history").json()
    assert len(after) == len(before) + 1
    event = after[-1]
    assert event["type"] == "disposition.approved"
    assert event["payload"]["source"]["instruction_text"] == "Approve case 1"
    assert event["payload"]["source"]["relayed_by"] == "mcp:centurion"


def test_a9_second_decision_409(case_stack: tuple[FastApiTestTransport, CaseApiClient]) -> None:
    transport, case_client = case_stack
    case_id = _proposed_case(transport)
    cfg = AdapterConfig(analyst_decisions_enabled=True, analyst_token="analyst-token")
    server = build_analyst_mcp_server(cfg, case_client=case_client)
    runner = _session_runner(server)
    args = {
        "case_id": case_id,
        "decision": "approve",
        "analyst_id": "vihag",
        "rationale": "ok",
        "chat_ref": "c",
        "instruction_text": "approve",
        "relayed_by": "centurion",
    }
    assert not runner.call_tool("record_analyst_decision", args).isError  # type: ignore[union-attr]
    second = runner.call_tool(
        "record_analyst_decision",
        {**args, "decision": "reject", "instruction_text": "reject"},
    )
    assert second.isError  # type: ignore[union-attr]
    client = httpx.Client(transport=transport, base_url="http://case.test")
    history = client.get(f"/v1/cases/{case_id}/history").json()
    events = [e for e in history if e["type"] == "disposition.approved"]
    assert len(events) == 1