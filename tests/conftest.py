"""Repository-wide test guards."""

from __future__ import annotations

import socket
from collections.abc import Generator

import httpx
import pytest

from services.triage.grok import HttpGrokClient

_ORIGINAL_HTTP_GROK_INIT = HttpGrokClient.__init__


def _is_mock_transport(transport: object | None) -> bool:
    return isinstance(transport, httpx.MockTransport)


@pytest.fixture(autouse=True)
def block_network(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if request.node.get_closest_marker("live"):
        yield
        return

    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo
    original_create_connection = socket.create_connection

    def _deny_connect(self: socket.socket, address: object) -> None:
        raise OSError("network disabled in tests")

    def _deny_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        raise OSError("network disabled in tests")

    def _deny_create_connection(*args: object, **kwargs: object) -> socket.socket:
        raise OSError("network disabled in tests")

    socket.socket.connect = _deny_connect  # type: ignore[method-assign]
    socket.getaddrinfo = _deny_getaddrinfo  # type: ignore[assignment]
    socket.create_connection = _deny_create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
        socket.create_connection = original_create_connection  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def guard_http_grok_client(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    live = request.node.get_closest_marker("live") is not None

    def guarded_init(
        self: HttpGrokClient,
        *,
        api_key: str,
        model: str,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not live and not _is_mock_transport(transport):
            raise RuntimeError(
                "HttpGrokClient requires pytest marker live or httpx.MockTransport"
            )
        _ORIGINAL_HTTP_GROK_INIT(
            self,
            api_key=api_key,
            model=model,
            base_url=base_url,
            transport=transport,
        )

    HttpGrokClient.__init__ = guarded_init  # type: ignore[method-assign]
    try:
        yield
    finally:
        HttpGrokClient.__init__ = _ORIGINAL_HTTP_GROK_INIT  # type: ignore[method-assign]