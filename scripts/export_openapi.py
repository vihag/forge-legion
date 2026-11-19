"""Write the committed OpenAPI specs from the live apps, or fail when one has drifted.

Usage:
    python scripts/export_openapi.py
    python scripts/export_openapi.py --check
"""

import asyncio
import difflib
import json
import sys
from pathlib import Path
from typing import Any, Literal, Never

from services.case_api.app import create_app as create_case_app
from services.case_api.store import Store
from services.mcp_adapter.analyst_server import build_analyst_mcp_server
from services.mcp_adapter.config import AdapterConfig
from services.mcp_adapter.server import build_mcp_server
from services.mock_soar.app import create_app as create_soar_app
from services.mock_soar.store import SoarStore
from services.mock_splunk.app import create_app as create_splunk_app

ROOT = Path(__file__).resolve().parents[1]
SpecName = Literal["case_api", "mock_splunk", "mock_soar"]

SPECS: dict[str, str] = {
    "case_api": "contracts/openapi.json",
    "mock_splunk": "contracts/mock_splunk.openapi.json",
    "mock_soar": "contracts/mock_soar.openapi.json",
}

MCP_TOOLS_PATH = "contracts/mcp_tools.json"
MCP_ANALYST_TOOLS_PATH = "contracts/mcp_analyst_tools.json"


def _case_api_openapi() -> str:
    app = create_case_app(db_path=":memory:")
    try:
        spec = app.openapi()
    finally:
        store: object = getattr(app.state, "store", None)
        if isinstance(store, Store):
            store.close()
    return json.dumps(spec, indent=2) + "\n"


def _mock_splunk_openapi() -> str:
    app = create_splunk_app()
    return json.dumps(app.openapi(), indent=2) + "\n"


def _mock_soar_openapi() -> str:
    app = create_soar_app(db_path=":memory:")
    try:
        spec = app.openapi()
    finally:
        store: object = getattr(app.state, "store", None)
        if isinstance(store, SoarStore):
            store.close()
    return json.dumps(spec, indent=2) + "\n"


async def _mcp_tools_payload() -> list[dict[str, Any]]:
    config = AdapterConfig()
    mcp = build_mcp_server(config)
    tools = await mcp.list_tools()
    items: list[dict[str, Any]] = []
    for tool in tools:
        items.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema,
            }
        )
    items.sort(key=lambda item: str(item["name"]))
    return items


async def _mcp_analyst_tools_payload() -> list[dict[str, Any]]:
    config = AdapterConfig()
    mcp = build_analyst_mcp_server(config)
    tools = await mcp.list_tools()
    items: list[dict[str, Any]] = []
    for tool in tools:
        items.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema,
            }
        )
    items.sort(key=lambda item: str(item["name"]))
    return items


def _mcp_tools_json() -> str:
    payload = asyncio.run(_mcp_tools_payload())
    return json.dumps(payload, indent=2) + "\n"


def _mcp_analyst_tools_json() -> str:
    payload = asyncio.run(_mcp_analyst_tools_payload())
    return json.dumps(payload, indent=2) + "\n"


def canonical_openapi(spec_name: str) -> str:
    name = _as_spec_name(spec_name)
    match name:
        case "case_api":
            return _case_api_openapi()
        case "mock_splunk":
            return _mock_splunk_openapi()
        case "mock_soar":
            return _mock_soar_openapi()
        case _ as unreachable:
            never_name: Never = unreachable
            raise RuntimeError(f"unhandled spec {never_name}")


def canonical_mcp_tools() -> str:
    return _mcp_tools_json()


def canonical_mcp_analyst_tools() -> str:
    return _mcp_analyst_tools_json()


def _as_spec_name(value: str) -> SpecName:
    if value in ("case_api", "mock_splunk", "mock_soar"):
        return value  # type: ignore[return-value]
    raise ValueError(f"unknown spec {value}")


def _check(relative: str, live: str, *, label: str) -> bool:
    path = ROOT / relative
    if not path.is_file():
        print(f"{relative} is missing", file=sys.stderr)
        return False
    committed = path.read_text(encoding="utf-8")
    if committed == live:
        return True
    print(f"{label}: {relative} does not match the live app", file=sys.stderr)
    diff = "".join(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            live.splitlines(keepends=True),
            fromfile=relative,
            tofile="live",
        )
    )
    sys.stderr.write(diff[:8000])
    return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    generated = {name: canonical_openapi(name) for name in SPECS}
    tools = canonical_mcp_tools()
    analyst_tools = canonical_mcp_analyst_tools()
    if "--check" in args:
        ok = True
        for name, relative in SPECS.items():
            ok = _check(relative, generated[name], label="OpenAPI drift") and ok
        ok = _check(MCP_TOOLS_PATH, tools, label="MCP tools drift") and ok
        ok = _check(MCP_ANALYST_TOOLS_PATH, analyst_tools, label="MCP analyst tools drift") and ok
        return 0 if ok else 1
    for name, relative in SPECS.items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated[name], encoding="utf-8")
        print(f"wrote {path}")
    tools_path = ROOT / MCP_TOOLS_PATH
    tools_path.write_text(tools, encoding="utf-8")
    print(f"wrote {tools_path}")
    analyst_path = ROOT / MCP_ANALYST_TOOLS_PATH
    analyst_path.write_text(analyst_tools, encoding="utf-8")
    print(f"wrote {analyst_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())