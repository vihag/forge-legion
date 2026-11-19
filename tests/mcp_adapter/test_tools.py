"""MCP adapter tool contract tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import MockTransport

from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig
from services.mcp_adapter.server import build_mcp_server
from tests.mcp_adapter.asgi_transport import FastApiTestTransport

CONTRACT_TOOLS = (
    "add_evidence",
    "get_case",
    "get_case_history",
    "list_cases",
    "propose_disposition",
    "record_finding",
    "request_soar_action",
)


def _payload(result: Any) -> dict[str, Any] | list[Any]:
    if result.isError:
        text = result.content[0].text
        return json.loads(text)
    structured: object = result.structuredContent
    if isinstance(structured, dict) and "items" in structured and len(structured) == 1:
        items: object = structured["items"]
        if isinstance(items, list):
            return items
    if isinstance(structured, dict):
        return structured
    if isinstance(structured, list):
        return structured
    text = result.content[0].text
    parsed: object = json.loads(text)
    assert isinstance(parsed, dict | list)
    return parsed  # type: ignore[return-value]


def test_tools_list_matches_contract(mcp_session: Any) -> None:
    tools = mcp_session.list_tools().tools
    names = sorted(tool.name for tool in tools)
    assert names == sorted(CONTRACT_TOOLS)
    assert "approve" not in names


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("list_cases", {"limit": 10, "offset": 0}),
        ("get_case", {"case_id": 1}),
        ("get_case_history", {"case_id": 1}),
        (
            "add_evidence",
            {
                "case_id": 1,
                "source": "s",
                "ref": "r",
                "content": {"a": 1},
                "actor_id": "evidence-sentinel",
            },
        ),
        (
            "record_finding",
            {
                "case_id": 1,
                "kind": "observation",
                "statement": "s",
                "evidence_ids": [1],
                "actor_id": "finding-agent",
            },
        ),
        (
            "propose_disposition",
            {
                "case_id": 1,
                "action": "contain",
                "rationale": "r",
                "finding_ids": [1],
                "actor_id": "disposition-sentinel",
            },
        ),
        ("request_soar_action", {"case_id": 1, "actor_id": "soar-bot"}),
    ],
)
def test_each_tool_makes_exactly_one_case_api_call(
    case_app: Any,
    adapter_config: AdapterConfig,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"ok": True})

    transport = MockTransport(handler)
    client = CaseApiClient(adapter_config.case_api_url, transport=transport)
    server = build_mcp_server(adapter_config, case_client=client)
    runner = _session_runner(server)
    runner.call_tool(tool_name, arguments)
    assert len(calls) == 1


def _session_runner(server: Any) -> Any:
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session

    class SessionRunner:
        def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            async def _inner() -> Any:
                async with create_connected_server_and_client_session(server) as session:
                    return await session.call_tool(name, arguments)

            return anyio.run(_inner)

    return SessionRunner()


def _ingest_case(transport: FastApiTestTransport, alert_id: str) -> int:
    client = httpx.Client(transport=transport, base_url="http://case.test")
    case = client.post(
            "/v1/cases",
            json={
                "alert_id": alert_id,
                "title": "t",
                "splunk_severity": "high",
                "summary": "s",
                "assigned_analyst": "sam",
                "actor": {"id": "splunk-watcher", "type": "system"},
            },
    ).json()
    return int(case["id"])


def _triage_case(transport: FastApiTestTransport, case_id: int) -> None:
    client = httpx.Client(transport=transport, base_url="http://case.test")
    client.post(
            f"/v1/cases/{case_id}/triage",
            json={
                "severity": "high",
                "rationale": "r",
                "model": "grok-3",
                "actor": {"id": "triage-agent", "type": "agent"},
            },
    )


@pytest.mark.parametrize(
    ("tool_name", "actor_id"),
    [
        ("add_evidence", "evidence-sentinel"),
        ("record_finding", "finding-agent"),
        ("propose_disposition", "disposition-sentinel"),
        ("request_soar_action", "soar-bot"),
    ],
)
def test_mutating_tools_write_one_case_api_event_with_mcp_actor(
    mcp_session: Any,
    case_transport: FastApiTestTransport,
    approved_case_id: int,
    tool_name: str,
    actor_id: str,
) -> None:
    if tool_name == "add_evidence":
        case_id = _ingest_case(case_transport, f"ALT-{tool_name}")
        _triage_case(case_transport, case_id)
        args = {
            "case_id": case_id,
            "source": "mock_splunk",
            "ref": "idx=auth",
            "content": {"k": "v"},
            "actor_id": actor_id,
        }
    elif tool_name == "record_finding":
        case_id = _ingest_case(case_transport, f"ALT-{tool_name}")
        _triage_case(case_transport, case_id)
        evidence = mcp_session.call_tool(
            "add_evidence",
            {
                "case_id": case_id,
                "source": "mock_splunk",
                "ref": "idx=auth",
                "content": {"k": "v"},
                "actor_id": "evidence-sentinel",
            },
        )
        evidence_id = _payload(evidence)["id"]
        args = {
            "case_id": case_id,
            "kind": "observation",
            "statement": "seen",
            "evidence_ids": [evidence_id],
            "actor_id": actor_id,
        }
    elif tool_name == "propose_disposition":
        case_id = _ingest_case(case_transport, f"ALT-{tool_name}")
        _triage_case(case_transport, case_id)
        evidence = mcp_session.call_tool(
            "add_evidence",
            {
                "case_id": case_id,
                "source": "s",
                "ref": "r",
                "content": {"x": 1},
                "actor_id": "evidence-sentinel",
            },
        )
        finding = mcp_session.call_tool(
            "record_finding",
            {
                "case_id": case_id,
                "kind": "observation",
                "statement": "s",
                "evidence_ids": [_payload(evidence)["id"]],
                "actor_id": "finding-agent",
            },
        )
        args = {
            "case_id": case_id,
            "action": "contain",
            "rationale": "r",
            "finding_ids": [_payload(finding)["id"]],
            "actor_id": actor_id,
        }
    elif tool_name == "request_soar_action":
        case_id = approved_case_id
        args = {"case_id": case_id, "actor_id": actor_id}
    else:
        raise AssertionError(tool_name)

    before = mcp_session.call_tool("get_case_history", {"case_id": case_id})
    before_count = len(_payload(before))
    result = mcp_session.call_tool(tool_name, args)
    assert not result.isError, result
    after = mcp_session.call_tool("get_case_history", {"case_id": case_id})
    events = _payload(after)
    assert len(events) == before_count + 1
    last = events[-1]
    assert last["actor"]["id"] == f"mcp:{actor_id}"
    assert last["actor"]["type"] == "agent"


def test_read_tools_write_no_event(mcp_session: Any, approved_case_id: int) -> None:
    before = mcp_session.call_tool("get_case_history", {"case_id": approved_case_id})
    before_count = len(_payload(before))
    mcp_session.call_tool("list_cases", {"limit": 10, "offset": 0})
    mcp_session.call_tool("get_case", {"case_id": approved_case_id})
    mcp_session.call_tool("get_case_history", {"case_id": approved_case_id})
    after = mcp_session.call_tool("get_case_history", {"case_id": approved_case_id})
    assert len(_payload(after)) == before_count


def test_request_soar_before_approval_returns_tool_error_409(
    case_transport: FastApiTestTransport,
    adapter_config: AdapterConfig,
) -> None:
    client = httpx.Client(transport=case_transport, base_url="http://case.test")
    case = client.post(
            "/v1/cases",
            json={
                "alert_id": "ALT-NO-APPROVE",
                "title": "t",
                "splunk_severity": "low",
                "summary": "s",
                "assigned_analyst": "sam",
                "actor": {"id": "splunk-watcher", "type": "system"},
            },
    ).json()
    case_id = case["id"]
    client = CaseApiClient("http://case.test", transport=case_transport)
    server = build_mcp_server(adapter_config, case_client=client)
    result = _session_runner(server).call_tool(
        "request_soar_action",
        {"case_id": case_id, "actor_id": "soar-bot"},
    )
    assert result.isError
    payload = json.loads(result.content[0].text)
    assert payload["status"] == 409
    assert payload["error"] == "illegal_transition"


def test_request_soar_after_analyst_approval_returns_soar_id(
    mcp_session: Any,
    approved_case_id: int,
) -> None:
    result = mcp_session.call_tool(
        "request_soar_action",
        {"case_id": approved_case_id, "actor_id": "soar-bot"},
    )
    assert not result.isError
    payload = _payload(result)
    assert "soar_case_id" in payload


def test_actor_type_cannot_be_overridden(
    case_transport: FastApiTestTransport,
    adapter_config: AdapterConfig,
) -> None:
    captured: list[dict[str, Any]] = []
    case_id = _ingest_case(case_transport, "ALT-ACTOR-TYPE")
    _triage_case(case_transport, case_id)

    def forward(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/evidence"):
            captured.append(json.loads(request.content.decode()))
        forward_client = httpx.Client(transport=case_transport, base_url="http://case.test")
        return forward_client.send(request)

    server = build_mcp_server(
        adapter_config,
        case_client=CaseApiClient("http://case.test", transport=MockTransport(forward)),
    )
    _session_runner(server).call_tool(
        "add_evidence",
        {
            "case_id": case_id,
            "source": "s",
            "ref": "r",
            "content": {"x": 1},
            "actor_id": "evidence-sentinel",
        },
    )
    assert captured
    assert captured[0]["actor"]["type"] == "agent"
    assert captured[0]["actor"]["id"] == "mcp:evidence-sentinel"


def test_actor_allowlist_rejects_unknown_before_call(adapter_config: AdapterConfig) -> None:
    config = AdapterConfig(
        case_api_url=adapter_config.case_api_url,
        adapter_token=None,
        actor_allowlist=frozenset({"allowed-bot"}),
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    server = build_mcp_server(
        config,
        case_client=CaseApiClient(config.case_api_url, transport=MockTransport(handler)),
    )
    result = _session_runner(server).call_tool(
        "add_evidence",
        {
            "case_id": 1,
            "source": "s",
            "ref": "r",
            "content": {},
            "actor_id": "other-bot",
        },
    )
    assert result.isError
    assert calls == []


def test_case_api_error_passed_through_unchanged(
    adapter_config: AdapterConfig,
    case_transport: FastApiTestTransport,
) -> None:
    server = build_mcp_server(
        adapter_config,
        case_client=CaseApiClient("http://case.test", transport=case_transport),
    )
    result = _session_runner(server).call_tool("get_case", {"case_id": 99999})
    assert result.isError
    payload = json.loads(result.content[0].text)
    assert payload["status"] == 404
    assert payload["error"] == "not_found"


def test_case_api_unreachable_returns_503_tool_error(adapter_config: AdapterConfig) -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    server = build_mcp_server(
        adapter_config,
        case_client=CaseApiClient(adapter_config.case_api_url, transport=MockTransport(boom)),
    )
    result = _session_runner(server).call_tool("list_cases", {})
    assert result.isError
    payload = json.loads(result.content[0].text)
    assert payload["status"] == 503
    assert payload["error"] == "case_api_unavailable"


def test_adapter_has_no_case_api_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "services" / "mcp_adapter"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("services.case_api"):
                        violations.append(f"{path.name}: import {alias.name}")
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("services.case_api"):
                    violations.append(f"{path.name}: from {node.module}")
                if node.module == "services":
                    for alias in node.names:
                        if alias.name == "case_api":
                            violations.append(f"{path.name}: from services import case_api")
    assert violations == []