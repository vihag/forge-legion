"""Write the committed OpenAPI specs from the live apps, or fail when one has drifted.

Usage:
    python scripts/export_openapi.py
    python scripts/export_openapi.py --check
"""

import difflib
import json
import sys
from pathlib import Path
from typing import Literal, Never

from services.case_api.app import create_app as create_case_app
from services.case_api.store import Store
from services.mock_splunk.app import create_app as create_splunk_app

ROOT = Path(__file__).resolve().parents[1]
SpecName = Literal["case_api", "mock_splunk"]

SPECS: dict[str, str] = {
    "case_api": "contracts/openapi.json",
    "mock_splunk": "contracts/mock_splunk.openapi.json",
}


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


def canonical_openapi(spec_name: str) -> str:
    name = _as_spec_name(spec_name)
    match name:
        case "case_api":
            return _case_api_openapi()
        case "mock_splunk":
            return _mock_splunk_openapi()
        case _ as unreachable:
            never_name: Never = unreachable
            raise RuntimeError(f"unhandled spec {never_name}")


def _as_spec_name(value: str) -> SpecName:
    if value == "case_api" or value == "mock_splunk":
        return value
    raise ValueError(f"unknown spec {value}")


def _check(relative: str, live: str) -> bool:
    path = ROOT / relative
    if not path.is_file():
        print(f"{relative} is missing", file=sys.stderr)
        return False
    committed = path.read_text(encoding="utf-8")
    if committed == live:
        return True
    print(f"OpenAPI drift: {relative} does not match the live app", file=sys.stderr)
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
    if "--check" in args:
        ok = True
        for name, relative in SPECS.items():
            ok = _check(relative, generated[name]) and ok
        return 0 if ok else 1
    for name, relative in SPECS.items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated[name], encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
