"""ASGI app: streamable HTTP MCP at /mcp with optional bearer auth."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from services.mcp_adapter.analyst_server import build_analyst_mcp_server
from services.mcp_adapter.case_client import CaseApiClient
from services.mcp_adapter.config import AdapterConfig, load_allowed_hosts, load_config
from services.mcp_adapter.server import build_mcp_server


class _NormalizeMcpPath:
    """Map POST /mcp and /mcp-analyst without trailing slash to their mounted paths."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            path = scope.get("path")
            if path in ("/mcp", "/mcp-analyst"):
                scope = dict(scope)
                scope["path"] = f"{path}/"
        await self.app(scope, receive, send)


class PublicFailClosedMiddleware:
    """Reject public-gateway requests when no adapter token is configured."""

    def __init__(self, app: Any, adapter_token: str | None) -> None:
        self.app = app
        self._adapter_token = adapter_token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if request.headers.get("x-forge-public", "").strip() == "1":
            if self._adapter_token is None:
                response = JSONResponse(
                    status_code=503,
                    content={"code": "public_requires_token"},
                )
                await response(scope, receive, send)
                return
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


def _wrap_bearer(inner: Any, token: str | None) -> Any:
    handler: Any = inner
    if token is not None:
        handler = BearerGateMiddleware(inner, token)
    return handler


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
    agent_inner = mcp.streamable_http_app()
    agent_handler = _wrap_bearer(agent_inner, cfg.adapter_token)

    analyst_mcp = None
    analyst_handler: Any = None
    if cfg.analyst_decisions_enabled:
        assert cfg.analyst_token is not None
        analyst_mcp = build_analyst_mcp_server(cfg, case_client=case_client)
        analyst_mcp.settings.streamable_http_path = "/"
        analyst_mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=load_allowed_hosts(),
        )
        analyst_inner = analyst_mcp.streamable_http_app()
        analyst_handler = _wrap_bearer(analyst_inner, cfg.analyst_token)

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def analyst_disabled(_request: Request) -> Response:
        return Response(status_code=404)

    @asynccontextmanager
    async def lifespan(_application: Starlette) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp.session_manager.run())
            if analyst_mcp is not None:
                await stack.enter_async_context(analyst_mcp.session_manager.run())
            yield

    routes: list[Route | Mount] = [
        Route("/healthz", healthz),
        Mount("/mcp", app=agent_handler),
    ]
    if analyst_handler is not None:
        routes.append(Mount("/mcp-analyst", app=analyst_handler))
    else:
        routes.append(Route("/mcp-analyst", analyst_disabled, methods=["GET", "POST"]))
        routes.append(Route("/mcp-analyst/", analyst_disabled, methods=["GET", "POST"]))

    application = Starlette(routes=routes, lifespan=lifespan)
    application.router.redirect_slashes = False
    wrapped = PublicFailClosedMiddleware(application, cfg.adapter_token)
    return _NormalizeMcpPath(wrapped)


def __getattr__(name: str) -> Any:
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")