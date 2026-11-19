"""Ops config invariants: crown jewels and nginx upstream re-resolution."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_routing_crown_jewels_match_world() -> None:
    world = yaml.safe_load((ROOT / "config/world.yaml").read_text(encoding="utf-8"))
    routing = yaml.safe_load((ROOT / "config/routing.yaml").read_text(encoding="utf-8"))
    jewels = {host["id"] for host in world["hosts"] if host["crown_jewel"]}
    assert set(routing["crown_jewel_hosts"]) == jewels
    assert jewels == {"FIN-DB-01", "DC-01", "DC-02", "JMP-01", "PAY-APP-02", "SWIFT-GW-01"}


def test_nginx_upstreams_resolve_at_request_time() -> None:
    text = (ROOT / "deploy/nginx/default.conf.template").read_text(encoding="utf-8")
    assert text.count("resolver 127.0.0.11") == text.count("server {")
    assert "proxy_pass http://${" not in text
