"""Gateway and backend health checks against PUBLIC_BASE_URL (stdlib only)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

INIT_PAYLOAD = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "health", "version": "0"},
        },
    },
).encode("utf-8")

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@dataclass(frozen=True)
class FetchResult:
    status: int
    history: tuple[Any, ...] = ()


FetchFn = Callable[[str, dict[str, str], bytes | None], FetchResult]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, fp, code, msg, headers, newurl
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_fetch(url: str, headers: dict[str, str], body: bytes | None) -> FetchResult:
    method = "POST" if body is not None else "GET"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request) as response:
            return FetchResult(status=response.getcode() or 0, history=())
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location")
        history = (location,) if location else ()
        return FetchResult(status=exc.code, history=history)
    except urllib.error.URLError:
        return FetchResult(status=0, history=())


def _check_json_ok(result: FetchResult) -> bool:
    return result.status == 200


CheckResult = tuple[str, bool, int]


def run_checks(
    base_url: str,
    fetch: FetchFn,
    *,
    bearer: str | None = None,
) -> list[CheckResult]:
    base = base_url.rstrip("/")
    auth_headers: dict[str, str] = {}
    if bearer:
        auth_headers["Authorization"] = f"Bearer {bearer}"
    checks: list[tuple[str, str, dict[str, str], bytes | None, Callable[[FetchResult], bool]]] = [
        ("gateway", f"{base}/healthz", {}, None, _check_json_ok),
        ("case", f"{base}/case/healthz", {}, None, _check_json_ok),
        ("splunk", f"{base}/splunk/healthz", {}, None, _check_json_ok),
        ("soar", f"{base}/soar/healthz", {}, None, _check_json_ok),
        ("mcp", f"{base}/mcp/healthz", {}, None, _check_json_ok),
    ]
    mcp_headers = {**MCP_HEADERS, **auth_headers}
    results: list[CheckResult] = []
    for name, url, headers, body, predicate in checks:
        merged = {**headers, **auth_headers}
        result = fetch(url, merged, body)
        results.append((name, predicate(result), result.status))
    mcp_result = fetch(f"{base}/mcp", mcp_headers, INIT_PAYLOAD)
    mcp_ok = mcp_result.status == 200 and not mcp_result.history
    results.append(("mcp_init", mcp_ok, mcp_result.status))
    return results


def main() -> int:
    base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080")
    timeout_s = float(os.environ.get("HEALTH_TIMEOUT_S", "60"))
    bearer = os.environ.get("MCP_ADAPTER_TOKEN") or None
    if bearer is not None and bearer.strip() == "":
        bearer = None

    deadline = time.monotonic() + timeout_s
    names = ["gateway", "case", "splunk", "soar", "mcp", "mcp_init"]
    last_results: list[CheckResult] = [(name, False, 0) for name in names]

    while time.monotonic() < deadline:
        last_results = run_checks(base_url, _urllib_fetch, bearer=bearer)
        if all(ok for _, ok, _ in last_results):
            for name, _, code in last_results:
                if name == "mcp_init":
                    continue
                print(f"PASS {name} {code}")
            print(f"PASS mcp_init {last_results[-1][2]}")
            return 0
        time.sleep(0.5)

    for name, ok, code in last_results:
        if name == "mcp_init":
            continue
        status = "PASS" if ok else "FAIL"
        print(f"{status} {name} {code}")
    name, ok, code = last_results[-1]
    status = "PASS" if ok else "FAIL"
    print(f"{status} mcp_init {code}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())