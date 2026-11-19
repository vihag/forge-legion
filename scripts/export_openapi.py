"""Write contracts/openapi.json from the live app, or fail when the file has drifted.

Usage:
    python scripts/export_openapi.py
    python scripts/export_openapi.py --check
"""

import difflib
import json
import sys
from pathlib import Path

from services.case_api.app import create_app
from services.case_api.store import Store

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "contracts" / "openapi.json"


def canonical_openapi() -> str:
    app = create_app(db_path=":memory:")
    try:
        spec = app.openapi()
    finally:
        store: object = getattr(app.state, "store", None)
        if isinstance(store, Store):
            store.close()
    return json.dumps(spec, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    text = canonical_openapi()
    if "--check" in args:
        if not SPEC_PATH.is_file():
            print("contracts/openapi.json is missing", file=sys.stderr)
            return 1
        committed = SPEC_PATH.read_text(encoding="utf-8")
        if committed != text:
            print(
                "OpenAPI drift: contracts/openapi.json does not match the live app",
                file=sys.stderr,
            )
            diff = "".join(
                difflib.unified_diff(
                    committed.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile="contracts/openapi.json",
                    tofile="live",
                )
            )
            sys.stderr.write(diff[:8000])
            return 1
        return 0
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
