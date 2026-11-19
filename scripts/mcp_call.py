"""Call one MCP tool over streamable HTTP."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Invoke one FORGE MCP tool")
    parser.add_argument("--base", required=True, help="Gateway base URL")
    parser.add_argument("--path", required=True, choices=("/mcp", "/mcp-analyst"))
    parser.add_argument("--token-env", required=True, help="Env var holding the bearer token")
    parser.add_argument("--tool", required=True, help="Tool name")
    parser.add_argument("--args", required=True, help="JSON object of tool arguments")
    return parser.parse_args(argv)


def _find_http_status_error(exc: BaseException) -> httpx.HTTPStatusError | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            found = _find_http_status_error(item)
            if found is not None:
                return found
    return None


async def _run_tool(
    *,
    url: str,
    token: str | None,
    tool: str,
    arguments: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    headers: dict[str, str] = {
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(headers=headers, timeout=60.0) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
    except BaseException as exc:
        status_error = _find_http_status_error(exc)
        if status_error is not None:
            return 4, {
                "status": status_error.response.status_code,
                "error": "http_error",
                "message": str(status_error),
            }
        if isinstance(exc, httpx.HTTPError):
            return 4, {"status": 0, "error": "http_error", "message": str(exc)}
        raise
    payload: dict[str, Any] = {
        "isError": result.isError,
        "content": [block.model_dump(mode="json") for block in result.content],
    }
    if result.structuredContent is not None:
        payload["structuredContent"] = result.structuredContent
    code = 3 if result.isError else 0
    return code, payload


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    token = os.environ.get(args.token_env)
    if token is not None and token.strip() == "":
        token = None
    try:
        arguments: object = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": "invalid_json", "message": str(exc)}))
        return 4
    if not isinstance(arguments, dict):
        print(json.dumps({"error": "invalid_json", "message": "args must be a JSON object"}))
        return 4
    base = args.base.rstrip("/")
    path = args.path.rstrip("/") + "/"
    url = f"{base}{path}"
    code, payload = asyncio.run(
        _run_tool(url=url, token=token, tool=args.tool, arguments=arguments)
    )
    print(json.dumps(payload, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())