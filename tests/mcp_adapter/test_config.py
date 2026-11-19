"""MCP_ALLOWED_HOSTS additive loading."""

import pytest

from services.mcp_adapter.config import load_allowed_hosts

DEFAULTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]


@pytest.fixture(autouse=True)
def _clear_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)


def test_allowed_hosts_defaults_when_unset() -> None:
    assert load_allowed_hosts() == DEFAULTS


def test_allowed_hosts_extras_are_added_not_replacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.example")
    hosts = load_allowed_hosts()
    assert hosts[:3] == DEFAULTS
    assert "a.example" in hosts
    assert "a.example:*" in hosts


def test_bare_host_adds_host_and_wildcard_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "tunnel.test")
    hosts = load_allowed_hosts()
    assert "tunnel.test" in hosts
    assert "tunnel.test:*" in hosts


def test_blank_entries_ignored_and_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.example,,a.example, b.example ")
    hosts = load_allowed_hosts()
    assert hosts.count("a.example") == 1
    assert "b.example" in hosts
    assert "b.example:*" in hosts
    assert hosts[0:3] == DEFAULTS