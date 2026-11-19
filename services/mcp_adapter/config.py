"""Environment configuration for the MCP adapter."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterConfig:
    case_api_url: str
    adapter_token: str | None
    actor_allowlist: frozenset[str] | None


def load_config() -> AdapterConfig:
    allowlist_raw = os.environ.get("MCP_ACTOR_ALLOWLIST", "").strip()
    allowlist: frozenset[str] | None
    if allowlist_raw:
        allowlist = frozenset(item.strip() for item in allowlist_raw.split(",") if item.strip())
    else:
        allowlist = None
    token = os.environ.get("MCP_ADAPTER_TOKEN")
    if token is not None and token.strip() == "":
        token = None
    return AdapterConfig(
        case_api_url=os.environ.get("CASE_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        adapter_token=token,
        actor_allowlist=allowlist,
    )


def _expand_host_entry(entry: str) -> list[str]:
    if ":" in entry or entry.startswith("["):
        return [entry]
    return [entry, f"{entry}:*"]


def load_allowed_hosts() -> list[str]:
    """Host patterns for MCP transport-security (DNS rebinding)."""
    defaults = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
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