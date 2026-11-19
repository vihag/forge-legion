"""Synchronous httpx transport that drives an in-process ASGI app."""

import httpx
from fastapi import FastAPI
from starlette.testclient import TestClient


class InProcessTransport(httpx.BaseTransport):
    """Sync ``httpx`` transport backed by Starlette's ``TestClient``."""

    def __init__(self, app: FastAPI) -> None:
        self._client = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._client.request(
            request.method,
            str(request.url),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
        )