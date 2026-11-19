"""Splunk read-only MCP tools (C6 §6.2 B1–B3)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
from httpx import MockTransport

from services.mcp_adapter.config import AdapterConfig
from services.mcp_adapter.server import build_mcp_server
from services.mcp_adapter.splunk_client import SplunkClient
from services.mock_splunk.app import create_app as create_splunk_app
from tests.mcp_adapter.asgi_transport import FastApiTestTransport
from tests.mcp_adapter.test_analyst import _session_runner

ALERT_ID = "SPL-2026-0001"
WINDOW_START = "2026-11-17T00:00:00Z"
WINDOW_END = "2026-11-19T00:00:00Z"


@pytest.fixture
def splunk_transport() -> FastApiTestTransport:
    return FastApiTestTransport(create_splunk_app())


def test_b1_splunk_tool_reads_seed(splunk_transport: FastApiTestTransport) -> None:
    splunk = SplunkClient("http://splunk.test", transport=splunk_transport)
    server = build_mcp_server(AdapterConfig(), splunk_client=splunk)
    runner = _session_runner(server)
    alert = runner.call_tool("get_alert", {"alert_id": ALERT_ID})
    assert not alert.isError  # type: ignore[union-attr]
    payload = json.loads(alert.content[0].text)  # type: ignore[union-attr]
    assert payload["alert_id"] == ALERT_ID
    evidence = runner.call_tool("list_alert_evidence", {"alert_id": ALERT_ID})
    ev_payload = json.loads(evidence.content[0].text)  # type: ignore[union-attr]
    assert len(ev_payload["items"]) == 7
    search = runner.call_tool(
        "search_evidence",
        {"source": "auth", "start": WINDOW_START, "end": WINDOW_END},
    )
    search_payload = json.loads(search.content[0].text)  # type: ignore[union-attr]
    ids = {item["evidence_id"] for item in search_payload["items"]}
    assert "EV-AUTH-0001" in ids


def test_b2_splunk_requests_are_get_only(splunk_transport: FastApiTestTransport) -> None:
    methods: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        client = httpx.Client(transport=splunk_transport, base_url="http://splunk.test")
        return client.send(request)

    splunk = SplunkClient("http://splunk.test", transport=MockTransport(record))
    server = build_mcp_server(AdapterConfig(), splunk_client=splunk)
    runner = _session_runner(server)
    runner.call_tool("get_alert", {"alert_id": ALERT_ID})
    runner.call_tool("list_alert_evidence", {"alert_id": ALERT_ID})
    runner.call_tool(
        "search_evidence",
        {"source": "auth", "start": WINDOW_START, "end": WINDOW_END},
    )
    assert methods == ["GET", "GET", "GET"]


def test_b2_ast_no_mock_splunk_import_and_splunk_client_get_only() -> None:
    adapter_root = Path(__file__).resolve().parents[2] / "services" / "mcp_adapter"
    for path in adapter_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("services.mock_splunk"), path.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("services.mock_splunk"), path.name
    splunk_path = adapter_root / "splunk_client.py"
    text = splunk_path.read_text(encoding="utf-8")
    for verb in (".post(", ".put(", ".delete("):
        assert verb not in text


def test_b3_missing_alert_not_found(splunk_transport: FastApiTestTransport) -> None:
    splunk = SplunkClient("http://splunk.test", transport=splunk_transport)
    server = build_mcp_server(AdapterConfig(), splunk_client=splunk)
    result = _session_runner(server).call_tool("get_alert", {"alert_id": "NOPE"})
    assert result.isError  # type: ignore[union-attr]
    payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert payload["error"] == "not_found"