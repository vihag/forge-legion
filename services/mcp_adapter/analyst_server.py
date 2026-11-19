"""FastMCP analyst tools — one Case API decision call each."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from contracts.models import Actor, DecisionSource
from services.mcp_adapter.case_client import (
    CaseApiClient,
    CaseApiHttpError,
    CaseApiUnreachable,
)
from services.mcp_adapter.config import AdapterConfig


def _tool_error(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))],
        isError=True,
    )


def _tool_ok(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))],
        structuredContent=payload,
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


def build_analyst_mcp_server(
    config: AdapterConfig,
    case_client: CaseApiClient | None = None,
) -> FastMCP:
    client = case_client if case_client is not None else CaseApiClient(config.case_api_url)
    mcp = FastMCP("FORGE Analyst MCP", json_response=True)

    @mcp.tool()
    def record_analyst_decision(
        case_id: int,
        decision: Literal["approve", "reject"],
        analyst_id: str,
        rationale: str,
        chat_ref: str,
        instruction_text: str,
        relayed_by: str,
        message_ref: str | None = None,
    ) -> CallToolResult:
        """Record an analyst approve/reject from chat with provenance."""
        if analyst_id not in config.analyst_allowlist:
            return _tool_error(
                {
                    "status": 403,
                    "error": "analyst_not_allowed",
                    "message": "analyst_id is not on the allowlist",
                    "details": {"analyst_id": analyst_id},
                }
            )
        if not chat_ref.strip() or not instruction_text.strip() or not relayed_by.strip():
            return _tool_error(
                {
                    "status": 422,
                    "error": "source_required",
                    "message": "chat_ref, instruction_text, and relayed_by are required",
                    "details": {},
                }
            )
        source = DecisionSource(
            channel="grok_bot_chat",
            chat_ref=chat_ref.strip(),
            message_ref=message_ref,
            instruction_text=instruction_text,
            relayed_by=f"mcp:{relayed_by.strip()}",
        )
        try:
            result = client.record_decision(
                case_id,
                decision=decision,
                rationale=rationale,
                actor=Actor(id=analyst_id, type="analyst"),
                source=source,
            )
        except CaseApiHttpError as exc:
            return _map_client_error(exc)
        except CaseApiUnreachable:
            return _map_unreachable()
        return _tool_ok(result)

    return mcp