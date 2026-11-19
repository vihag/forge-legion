"""Pydantic models for data-driven alert stream scenarios."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.models import Severity

Pool = Literal["routine", "hitl"]
OutcomeClass = Literal[
    "auto_benign",
    "auto_fp",
    "hitl_confirm",
    "hitl_downgrade",
    "hitl_upgrade",
]
EntityKind = Literal["user", "host", "domain"]
EvidenceSource = Literal["auth", "vpn", "proxy", "edr", "firewall", "dns", "email", "cloud"]
StreamMode = Literal["random", "script"]


class OrgSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    timezone: str = Field(min_length=1)


class UserSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    dept: str = Field(min_length=1)
    privileged: bool
    home_country: str = Field(min_length=2, max_length=2)
    pool: Pool


class HostSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    dept: str = Field(min_length=1)
    os: str = Field(min_length=1)
    crown_jewel: bool
    pool: Pool


class DomainSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1)
    sanctioned: bool
    purpose: str = Field(min_length=1)


class WorldFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    org: OrgSpec
    users: list[UserSpec] = Field(min_length=1)
    hosts: list[HostSpec] = Field(min_length=1)
    domains: list[DomainSpec] = Field(default_factory=list)
    internal_cidrs: list[str] = Field(default_factory=list)


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(pattern=r"^(user|host|domain):[a-zA-Z0-9._-]+$")


class EntityPick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EntityKind
    where: dict[str, Any] = Field(default_factory=dict)


class EntityBinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str | None = None
    pick: EntityPick | None = None


class AlertFields(BaseModel):
    model_config = ConfigDict(extra="allow")


class ScenarioAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)


class ScenarioEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: EvidenceSource
    sourcetype: str = Field(min_length=1)
    offset_s: int
    fields: dict[str, Any] = Field(default_factory=dict)


class ScenarioFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    title: str = Field(min_length=1)
    splunk_rule_name: str = Field(min_length=1)
    splunk_severity: Severity
    outcome_class: OutcomeClass
    weight: float = Field(ge=0)
    demo_slot: int | None = Field(default=None, ge=0)
    assessed_severity: Severity | None = None
    mitre_techniques: list[str] = Field(default_factory=list)
    entities: dict[str, EntityBinding] = Field(default_factory=dict)
    alert: ScenarioAlert
    evidence: list[ScenarioEvidence] = Field(default_factory=list)
    analyst_knowledge: Any | None = None


class Scenario(BaseModel):
    """Runtime scenario (stripped of analyst-only fields)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    splunk_rule_name: str
    splunk_severity: Severity
    outcome_class: OutcomeClass
    weight: float
    demo_slot: int | None
    assessed_severity: Severity | None
    mitre_techniques: list[str]
    entities: dict[str, EntityBinding]
    alert: ScenarioAlert
    evidence: list[ScenarioEvidence]


class StreamStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: StreamMode = "random"
    rate_per_min: int | None = Field(default=None, ge=1)
    seed: int | None = None