"""Environment-backed configuration for the triage CLI and smoke script."""

import os
from dataclasses import dataclass
from pathlib import Path

from services.triage.routing import DEFAULT_ROUTING_PATH, RoutingConfig, load_routing


class ConfigError(Exception):
    """Misconfiguration that must fail before side effects."""


@dataclass(frozen=True)
class TriageSettings:
    xai_api_key: str | None
    xai_model: str
    xai_base_url: str
    case_api_url: str
    mock_splunk_url: str
    routing_config: RoutingConfig
    routing_path: Path


def load_settings() -> TriageSettings:
    routing_path = Path(
        os.environ.get("ROUTING_CONFIG", str(DEFAULT_ROUTING_PATH)),
    )
    return TriageSettings(
        xai_api_key=_optional_env("XAI_API_KEY"),
        xai_model=os.environ.get("XAI_MODEL", "grok-3-mini"),
        xai_base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
        case_api_url=os.environ.get("CASE_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        mock_splunk_url=os.environ.get("MOCK_SPLUNK_URL", "http://127.0.0.1:8001").rstrip("/"),
        routing_config=load_routing(routing_path),
        routing_path=routing_path,
    )


def require_api_key(settings: TriageSettings) -> str:
    if not settings.xai_api_key:
        raise ConfigError("XAI_API_KEY is not set")
    return settings.xai_api_key


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value