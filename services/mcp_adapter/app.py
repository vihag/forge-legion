"""ASGI app: streamable HTTP MCP at /mcp with optional bearer auth."""

from __future__ import annotations

from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig, load_config
from services.mcp_adapter.server import build_mcp_server


class BearerGateMiddleware:
    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        auth = request.headers.get("authorization")
        if auth != self._expected:
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "bearer token required",
                    "details": {},
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(
    config: AdapterConfig | None = None,
    case_client: CaseApiClient | None = None,
) -> Starlette:
    """Build the MCP adapter ASGI application."""
    cfg = config if config is not None else load_config()
    mcp = build_mcp_server(cfg, case_client=case_client)
    streamable_app: Any = mcp.streamable_http_app()
    if cfg.adapter_token is not None:
        streamable_app = BearerGateMiddleware(streamable_app, cfg.adapter_token)
    return Starlette(
        routes=[Mount("/mcp", app=streamable_app)],
    )


def __getattr__(name: str) -> Starlette:
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")