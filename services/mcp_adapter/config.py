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
        case_api_url=os.environ.get("CASE_API_URL", "http://127.0.0.1:8001").rstrip("/"),
        adapter_token=token,
        actor_allowlist=allowlist,
    )