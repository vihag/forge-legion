"""Environment-backed configuration for the triage CLI and smoke script."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from services.triage.routing import DEFAULT_ROUTING_PATH, RoutingConfig, load_routing


class ConfigError(Exception):
    """Misconfiguration that must fail before side effects."""


@dataclass(frozen=True)
class TriageSettings:
    xai_api_key: str | None = field(repr=False)
    xai_model: str
    xai_base_url: str
    case_api_url: str
    mock_splunk_url: str
    routing_config: RoutingConfig
    routing_path: Path
    grok_client: str = "http"
    grok_fixture_path: Path | None = None
    triage_worker_poll_s: int = 5
    triage_worker_concurrency: int = 1


def load_settings() -> TriageSettings:
    routing_path = Path(
        os.environ.get("ROUTING_CONFIG", str(DEFAULT_ROUTING_PATH)),
    )
    fixture_raw = os.environ.get("GROK_FIXTURE_PATH", "").strip()
    fixture_path = Path(fixture_raw) if fixture_raw else None
    return TriageSettings(
        xai_api_key=_optional_env("XAI_API_KEY"),
        xai_model=os.environ.get("XAI_MODEL", "grok-3-mini"),
        xai_base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
        case_api_url=os.environ.get("CASE_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        mock_splunk_url=os.environ.get("MOCK_SPLUNK_URL", "http://127.0.0.1:8001").rstrip("/"),
        grok_client=os.environ.get("GROK_CLIENT", "http").lower(),
        grok_fixture_path=fixture_path,
        routing_config=load_routing(routing_path),
        routing_path=routing_path,
        triage_worker_poll_s=int(os.environ.get("TRIAGE_WORKER_POLL_S", "5")),
        triage_worker_concurrency=int(os.environ.get("TRIAGE_WORKER_CONCURRENCY", "1")),
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