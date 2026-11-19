"""Sync httpx transport that forwards to FastAPI via TestClient."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient


class FastApiTestTransport(httpx.BaseTransport):
    def __init__(self, app: Any) -> None:
        self._client = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query.decode()}"
        response = self._client.request(
            request.method,
            path,
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )