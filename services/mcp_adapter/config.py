"""Environment configuration for the MCP adapter."""

import os
import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdapterConfig:
    case_api_url: str = "http://127.0.0.1:8000"
    splunk_url: str = "http://127.0.0.1:8001"
    adapter_token: str | None = None
    actor_allowlist: frozenset[str] | None = None
    analyst_decisions_enabled: bool = False
    analyst_token: str | None = None
    analyst_allowlist: frozenset[str] = field(default_factory=lambda: frozenset({"vihag"}))


def _optional_token(raw: str | None) -> str | None:
    if raw is None or raw.strip() == "":
        return None
    return raw


def _flag(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip() in ("1", "true", "True", "yes", "YES")


def load_config() -> AdapterConfig:
    allowlist_raw = os.environ.get("MCP_ACTOR_ALLOWLIST", "").strip()
    allowlist: frozenset[str] | None
    if allowlist_raw:
        allowlist = frozenset(item.strip() for item in allowlist_raw.split(",") if item.strip())
    else:
        allowlist = None
    analyst_allow_raw = os.environ.get("MCP_ANALYST_ALLOWLIST", "vihag").strip()
    analyst_allowlist = frozenset(
        item.strip() for item in analyst_allow_raw.split(",") if item.strip()
    )
    analyst_enabled = _flag(os.environ.get("MCP_ANALYST_DECISIONS_ENABLED"))
    adapter_token = _optional_token(os.environ.get("MCP_ADAPTER_TOKEN"))
    analyst_token = _optional_token(os.environ.get("MCP_ANALYST_TOKEN"))
    if analyst_enabled:
        if analyst_token is None:
            print("MCP_ANALYST_DECISIONS_ENABLED requires MCP_ANALYST_TOKEN", file=sys.stderr)
            raise SystemExit(1)
        if adapter_token is not None and analyst_token == adapter_token:
            print("MCP_ANALYST_TOKEN must differ from MCP_ADAPTER_TOKEN", file=sys.stderr)
            raise SystemExit(1)
    return AdapterConfig(
        case_api_url=os.environ.get("CASE_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        splunk_url=os.environ.get("MOCK_SPLUNK_URL", "http://127.0.0.1:8001").rstrip("/"),
        adapter_token=adapter_token,
        actor_allowlist=allowlist,
        analyst_decisions_enabled=analyst_enabled,
        analyst_token=analyst_token,
        analyst_allowlist=analyst_allowlist,
    )


def _expand_host_entry(entry: str) -> list[str]:
    if ":" in entry or entry.startswith("["):
        return [entry]
    return [entry, f"{entry}:*"]


def load_allowed_hosts() -> list[str]:
    """Host patterns for MCP transport-security (DNS rebinding)."""
    defaults = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
    ]
    raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    extras: list[str] = []
    if raw:
        for item in raw.split(","):
            stripped = item.strip()
            if not stripped:
                continue
            extras.extend(_expand_host_entry(stripped))
    seen: set[str] = set()
    merged: list[str] = []
    for host in [*defaults, *extras]:
        if host in seen:
            continue
        seen.add(host)
        merged.append(host)
    return merged