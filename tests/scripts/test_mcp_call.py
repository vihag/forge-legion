"""scripts/mcp_call.py exit codes."""

from __future__ import annotations

import httpx
from scripts.mcp_call import _find_http_status_error  # noqa: I001


def test_base_exception_group_unwraps_http_status() -> None:
    request = httpx.Request("GET", "http://example/mcp")
    response = httpx.Response(403, request=request)
    inner = httpx.HTTPStatusError("forbidden", request=request, response=response)
    group = BaseExceptionGroup("task failed", [inner])
    found = _find_http_status_error(group)
    assert found is not None
    assert found.response.status_code == 403