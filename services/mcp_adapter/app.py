"""ASGI app: streamable HTTP MCP at /mcp with optional bearer auth."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig, load_allowed_hosts, load_config
from services.mcp_adapter.server import build_mcp_server


class _NormalizeMcpPath:
    """Map POST /mcp to /mcp/ so Mount matches without a 307 redirect."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
        await self.app(scope, receive, send)


class BearerGateMiddleware:
    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        auth = request.headers.get("authorization")
        if not _bearer_matches(auth, self._token):
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


def _bearer_matches(header: str | None, expected_token: str) -> bool:
    if header is None:
        return False
    scheme, separator, credential = header.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(credential, expected_token)


def create_app(
    config: AdapterConfig | None = None,
    case_client: CaseApiClient | None = None,
) -> Any:
    """Build the MCP adapter ASGI application."""
    cfg = config if config is not None else load_config()
    mcp = build_mcp_server(cfg, case_client=case_client)
    mcp.settings.streamable_http_path = "/"
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=load_allowed_hosts(),
    )
    inner = mcp.streamable_http_app()
    handler: Any = inner
    if cfg.adapter_token is not None:
        handler = BearerGateMiddleware(inner, cfg.adapter_token)

    @asynccontextmanager
    async def lifespan(_application: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    application = Starlette(
        routes=[Mount("/mcp", app=handler)],
        lifespan=lifespan,
    )
    application.router.redirect_slashes = False
    return _NormalizeMcpPath(application)


def __getattr__(name: str) -> Any:
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")