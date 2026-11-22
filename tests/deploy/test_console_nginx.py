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
    assert "location = /console/config.js" in local_block
    assert "location = /console/config.js" not in public_tail


def test_compose_passes_grokbot_link_template_to_nginx() -> None:
    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")
    assert "GROKBOT_CENTURION_LINK_TEMPLATE:" in compose
    assert "grokbot://app/v1/agent?id=8818388d-d88b-4b48-a907-11dcc6b8547d" in compose