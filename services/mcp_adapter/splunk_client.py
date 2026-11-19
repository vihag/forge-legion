"""HTTP client for Mock Splunk GET endpoints only."""

from __future__ import annotations

from typing import Any, cast

import httpx


class SplunkHttpError(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("message", "Splunk error"))
        self.status = status
        self.body = body


class SplunkUnreachable(Exception):
    """Mock Splunk transport failed."""


class SplunkClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        client = httpx.Client(transport=self._transport, timeout=self._timeout)
        try:
            try:
                response = client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise SplunkUnreachable from exc
            try:
                body: object = response.json()
            except Exception:
                body = {"error": "error", "message": response.text, "details": {}}
            if response.status_code >= 400:
                if not isinstance(body, dict):
                    body = {"error": "error", "message": "invalid response", "details": {}}
                raise SplunkHttpError(response.status_code, body)
            if not isinstance(body, dict):
                body = {"error": "error", "message": "invalid response", "details": {}}
            return cast(dict[str, Any], body)
        finally:
            if self._transport is None:
                client.close()

    def get_alert(self, alert_id: str) -> dict[str, Any]:
        return self._get(f"/v1/alerts/{alert_id}")

    def list_alert_evidence(
        self, alert_id: str, *, source: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] | None = None
        if source is not None:
            params = {"source": source}
        return self._get(f"/v1/alerts/{alert_id}/evidence", params=params)

    def search_evidence(
        self,
        *,
        source: str,
        start: str,
        end: str,
        entity: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"source": source, "start": start, "end": end}
        if entity is not None:
            params["entity"] = entity
        return self._get("/v1/evidence", params=params)