"""Console static hosting on the local gateway only (C7 C1 partial)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "deploy" / "nginx" / "default.conf.template"


def test_console_only_on_local_gateway_block() -> None:
    text = NGINX.read_text(encoding="utf-8")
    local_block, public_tail = text.split("listen ${PUBLIC_GATEWAY_PORT}", 1)
    assert "location /console/" in local_block
    assert "location = /console" in local_block
    assert "return 302 /console/" in local_block
    assert "location /console/" not in public_tail
    assert "location = /console" not in public_tail