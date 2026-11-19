"""FastMCP tool definitions — one HTTP call each, no business logic."""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from contracts.mcp_tools import MCP_ACTOR_ID_PATTERN
from services.mcp_adapter.case_client import (
    CaseApiClient,
    CaseApiHttpError,
    CaseApiUnreachable,
    mcp_actor,
)
from services.mcp_adapter.config import AdapterConfig
from services.mcp_adapter.splunk_client import SplunkClient, SplunkHttpError, SplunkUnreachable

_ACTOR_RE = re.compile(MCP_ACTOR_ID_PATTERN)


def _tool_error(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))],
        isError=True,
    )


def _tool_ok(payload: dict[str, Any] | list[Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))],
        structuredContent=payload if isinstance(payload, dict) else {"items": payload},
        isError=False,
    )


def _map_client_error(exc: CaseApiHttpError) -> CallToolResult:
    return _tool_error(exc.as_tool_payload())


def _map_unreachable() -> CallToolResult:
    return _tool_error(
        {
            "status": 503,
            "error": "case_api_unavailable",
            "message": "Case API is unreachable",
            "details": {},
        }
    )


def _map_splunk_error(exc: SplunkHttpError) -> CallToolResult:
    if exc.status == 404:
        return _tool_error(
            {
                "status": 404,
                "error": "not_found",
                "message": exc.body.get("message", "not found"),
                "details": exc.body.get("details", {}),
            }
        )
    return _tool_error(
        {
            "status": exc.status,
            "error": exc.body.get("error", "error"),
            "message": exc.body.get("message", ""),
            "details": exc.body.get("details", {}),
        }
    )


def _map_splunk_unreachable() -> CallToolResult:
    return _tool_error(
        {
            "status": 503,
            "error": "splunk_unreachable",
            "message": "Mock Splunk is unreachable",
            "details": {},
        }
    )


def _check_actor(actor_id: str, config: AdapterConfig) -> CallToolResult | None:
    if not _ACTOR_RE.fullmatch(actor_id):
        return _tool_error(
            {
                "status": 422,
                "error": "validation_error",
                "message": "invalid actor_id",
                "details": {"actor_id": actor_id},
            }
        )
    if config.actor_allowlist is not None and actor_id not in config.actor_allowlist:
        return _tool_error(
            {
                "status": 403,
                "error": "actor_not_allowed",
                "message": "actor_id is not on the allowlist",
                "details": {"actor_id": actor_id},
            }
        )
    return None


def _run(call: Any) -> CallToolResult:
    try:
        result: dict[str, Any] | list[Any] = call()
        return _tool_ok(result)
    except CaseApiHttpError as exc:
        return _map_client_error(exc)
    except CaseApiUnreachable:
        return _map_unreachable()


def build_mcp_server(
    config: AdapterConfig,
    case_client: CaseApiClient | None = None,
    splunk_client: SplunkClient | None = None,
) -> FastMCP:
    client = case_client if case_client is not None else CaseApiClient(config.case_api_url)
    splunk = (
        splunk_client
        if splunk_client is not None
        else SplunkClient(config.splunk_url)
    )
    mcp = FastMCP("FORGE Case MCP", json_response=True)

    @mcp.tool()
    def list_cases(
        status: str | None = None,
        severity: str | None = None,
        alert_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> CallToolResult:
        """List cases with optional filters."""
        return _run(
            lambda: client.list_cases(
                status=status,
                severity=severity,
                alert_id=alert_id,
                limit=limit,
                offset=offset,
            )
        )

    @mcp.tool()
    def get_case(case_id: int) -> CallToolResult:
        """Fetch the full case view."""
        return _run(lambda: client.get_case(case_id))

    @mcp.tool()
    def get_case_history(case_id: int) -> CallToolResult:
        """Return audit history for a case."""
        return _run(lambda: client.get_case_history(case_id))

    @mcp.tool()
    def add_evidence(
        case_id: int,
        source: str,
        ref: str,
        content: dict[str, Any],
        actor_id: str,
    ) -> CallToolResult:
        """Add evidence to a case."""
        denied = _check_actor(actor_id, config)
        if denied is not None:
            return denied
        return _run(
            lambda: client.add_evidence(
                case_id,
                source=source,
                ref=ref,
                content=content,
                actor=mcp_actor(actor_id),
            )
        )

    @mcp.tool()
    def record_finding(
        case_id: int,
        kind: str,
        statement: str,
        evidence_ids: list[int],
        actor_id: str,
        confidence: float | None = None,
    ) -> CallToolResult:
        """Record a finding on a case."""
        denied = _check_actor(actor_id, config)
        if denied is not None:
            return denied
        return _run(
            lambda: client.record_finding(
                case_id,
                kind=kind,
                statement=statement,
                evidence_ids=evidence_ids,
                confidence=confidence,
                actor=mcp_actor(actor_id),
            )
        )

    @mcp.tool()
    def propose_disposition(
        case_id: int,
        action: str,
        rationale: str,
        finding_ids: list[int],
        actor_id: str,
    ) -> CallToolResult:
        """Propose a disposition for analyst approval."""
        denied = _check_actor(actor_id, config)
        if denied is not None:
            return denied
        return _run(
            lambda: client.propose_disposition(
                case_id,
                action=action,
                rationale=rationale,
                finding_ids=finding_ids,
                actor=mcp_actor(actor_id),
            )
        )

    @mcp.tool()
    def request_soar_action(case_id: int, actor_id: str) -> CallToolResult:
        """Submit an approved case to SOAR."""
        denied = _check_actor(actor_id, config)
        if denied is not None:
            return denied
        return _run(lambda: client.request_soar_action(case_id, actor=mcp_actor(actor_id)))

    @mcp.tool()
    def get_alert(alert_id: str) -> CallToolResult:
        """Fetch a Mock Splunk alert by id."""
        try:
            return _tool_ok(splunk.get_alert(alert_id))
        except SplunkHttpError as exc:
            return _map_splunk_error(exc)
        except SplunkUnreachable:
            return _map_splunk_unreachable()

    @mcp.tool()
    def list_alert_evidence(alert_id: str, source: str | None = None) -> CallToolResult:
        """List evidence linked to an alert."""
        try:
            return _tool_ok(splunk.list_alert_evidence(alert_id, source=source))
        except SplunkHttpError as exc:
            return _map_splunk_error(exc)
        except SplunkUnreachable:
            return _map_splunk_unreachable()

    @mcp.tool()
    def search_evidence(
        source: str,
        start: str,
        end: str,
        entity: str | None = None,
    ) -> CallToolResult:
        """Search Mock Splunk evidence in a time window."""
        try:
            return _tool_ok(
                splunk.search_evidence(source=source, start=start, end=end, entity=entity)
            )
        except SplunkHttpError as exc:
            return _map_splunk_error(exc)
        except SplunkUnreachable:
            return _map_splunk_unreachable()

    return mcp