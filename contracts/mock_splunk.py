"""Pydantic models for the read-only Mock Splunk API."""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AwareDatetime, Field, PlainSerializer

from contracts.models import APIModel, Severity
from contracts.scenarios import StreamMode, StreamStartRequest

EVIDENCE_ID_PATTERN = r"^EV-[A-Z]+-\d{4}(-\d+)?$"
MITRE_TECHNIQUE_PATTERN = r"^T\d{4}(\.\d{3})?$"
COUNTRY_CODE_PATTERN = r"^[A-Z]{2}$"

MitreTechnique = Annotated[str, Field(pattern=MITRE_TECHNIQUE_PATTERN)]


def format_utc(value: datetime) -> str:
    """ISO-8601 UTC with a Z suffix. Fractional seconds are kept when present."""
    utc = value.astimezone(UTC)
    text = utc.isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


UtcTimestamp = Annotated[
    AwareDatetime,
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]


class Entities(APIModel):
    user: str | None = None
    src_ip: str | None = None
    src_country: str | None = Field(default=None, pattern=COUNTRY_CODE_PATTERN)
    hosts: list[str] = Field(default_factory=list)


class Alert(APIModel):
    seq: int = Field(ge=1)
    alert_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    splunk_severity: Severity
    summary: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)
    mitre_techniques: list[MitreTechnique]
    triggered_at: UtcTimestamp
    emitted_at: UtcTimestamp
    replay_of: str | None = None
    entities: Entities
    scenario_id: str | None = None


class SplunkEvidence(APIModel):
    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    source: str = Field(min_length=1)
    timestamp: UtcTimestamp
    alert_ids: list[str]
    entities: Entities
    content: dict[str, Any]


class AlertPage(APIModel):
    items: list[Alert]
    next_after_seq: int = Field(ge=0)


class EvidenceList(APIModel):
    items: list[SplunkEvidence]


class SourceSummary(APIModel):
    name: str
    id_prefix: str
    record_count: int = Field(ge=0)


class SourceList(APIModel):
    items: list[SourceSummary]


class ResetRequest(APIModel):
    """Reset body; ``seed_hero: false`` skips the fixture alert(s). Extra fields rejected."""

    seed_hero: bool = True


class ResetResponse(APIModel):
    seed_version: str
    alerts: int = Field(ge=0)
    evidence: int = Field(ge=0)


class ReplayRequest(APIModel):
    alert_id: str = Field(min_length=1)


class StreamStatus(APIModel):
    running: bool
    mode: str | None = None
    rate_per_min: int = Field(ge=0)
    seed: int
    emitted: int = Field(ge=0)
    next_index: int = Field(ge=0)
    hitl_used: int = Field(ge=0)
    hitl_total: int = Field(ge=0)


class StreamStopRequest(APIModel):
    """Empty JSON object for stream stop."""


__all__ = [
    "Alert",
    "AlertPage",
    "Entities",
    "EvidenceList",
    "MitreTechnique",
    "ReplayRequest",
    "ResetRequest",
    "ResetResponse",
    "SourceList",
    "SourceSummary",
    "SplunkEvidence",
    "StreamMode",
    "StreamStartRequest",
    "StreamStatus",
    "StreamStopRequest",
    "format_utc",
]


