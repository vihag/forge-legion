"""Gateway and backend health checks against PUBLIC_BASE_URL (stdlib only)."""

from __future__ import annotations

import argparse
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


def _health_timeout_from_file(env_file: str) -> str | None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / env_file
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("HEALTH_TIMEOUT_S="):
            return line.split("=", 1)[1].strip()
    return None


def resolve_timeout(
    *,
    cli_flag: float | None,
    env_value: str | None,
    file_value: str | None,
) -> float:
    """Precedence: CLI flag > shell env > env file > default 60."""
    if cli_flag is not None:
        return cli_flag
    if env_value is not None and env_value.strip() != "":
        return float(env_value)
    if file_value is not None and file_value.strip() != "":
        return float(file_value)
    return 60.0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FORGE gateway health checks")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Overall timeout in seconds (overrides HEALTH_TIMEOUT_S)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import sys

    cli_argv = sys.argv[1:] if argv is None else argv
    args = _parse_args(cli_argv)
    base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080")
    file_timeout = _health_timeout_from_file(os.environ.get("ENV_FILE", ".env.local"))
    timeout_s = resolve_timeout(
        cli_flag=args.timeout,
        env_value=os.environ.get("HEALTH_TIMEOUT_S"),
        file_value=file_timeout,
    )
    bearer = os.environ.get("MCP_ADAPTER_TOKEN") or None
    if bearer is not None and bearer.strip() == "":
        bearer = None

    deadline = time.monotonic() + max(timeout_s, 0.0)
    last_results: list[CheckResult] = []

    while True:
        last_results = run_checks(base_url, _urllib_fetch, bearer=bearer)
        if all(ok for _, ok, _ in last_results):
            for name, _, code in last_results:
                if name == "mcp_init":
                    continue
                print(f"PASS {name} {code}")
            print(f"PASS mcp_init {last_results[-1][2]}")
            return 0
        if time.monotonic() >= deadline:
            break
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