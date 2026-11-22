"""Static guards for console JavaScript (GET-only, no case innerHTML)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "console"

NON_GET_METHOD = re.compile(r"""method:\s*['"](POST|PUT|PATCH|DELETE)""")
INNER_HTML_ASSIGN = re.compile(r"\.innerHTML\s*=")


def test_console_js_uses_get_only_for_fetch() -> None:
    for path in sorted(CONSOLE.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        matches = NON_GET_METHOD.findall(text)
        assert not matches, f"{path.name} uses non-GET fetch methods: {matches}"


def test_console_js_does_not_assign_innerhtml() -> None:
    for path in sorted(CONSOLE.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert not INNER_HTML_ASSIGN.search(text), f"{path.name} assigns innerHTML"