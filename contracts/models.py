"""Pydantic models for the case API. This module is the shared contract."""

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["low", "medium", "high", "critical"]
ActorType = Literal["analyst", "agent", "system"]
FindingKind = Literal["observation", "hypothesis"]
DispositionAction = Literal["false_positive", "benign", "escalate", "contain"]
DecisionValue = Literal["approve", "reject"]
Verdict = Literal["benign", "false_positive", "suspicious", "malicious"]
RecommendedAction = Literal["none", "escalate", "contain"]
Route = Literal["collaborative", "autonomous", "review"]


class CaseStatus(StrEnum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    INVESTIGATING = "INVESTIGATING"
    DISPOSITION_PROPOSED = "DISPOSITION_PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    AUTO_RESOLVED = "AUTO_RESOLVED"


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Actor(APIModel):
    id: str = Field(min_length=1, max_length=64)
    type: ActorType


class CaseCreate(APIModel):
    alert_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    splunk_severity: Severity
    summary: str | None = Field(default=None, min_length=1)
    assigned_analyst: str | None = Field(default=None, min_length=1)
    alert_time: str | None = Field(default=None, min_length=1)
    actor: Actor


class TriageCreate(APIModel):
    severity: Severity
    rationale: str = Field(min_length=1)
    model: str = Field(min_length=1)
    actor: Actor
    model_requested: str | None = Field(default=None, min_length=1)
    route: Route | None = None
    rule: str | None = Field(default=None, min_length=1)
    verdict: Verdict | None = None
    recommended_action: RecommendedAction | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    privileged_account: bool | None = None
    crown_jewel_asset: bool | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class AutoResolveCreate(APIModel):
    policy_id: Literal["autonomous-triage"]
    triage_evidence_id: int = Field(ge=1)
    actor: Actor


ReviewFlagReason = Literal["model_call_failed", "invalid_model_output", "no_route_matched"]


class ReviewFlagCreate(APIModel):
    reason: ReviewFlagReason
    detail: str = Field(min_length=1, max_length=2000)
    actor: Actor


class EvidenceCreate(APIModel):
    source: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    content: dict[str, Any]
    actor: Actor


class FindingCreate(APIModel):
    kind: FindingKind
    statement: str = Field(min_length=1)
    evidence_ids: list[int]
    confidence: float | None = Field(default=None, ge=0, le=1)
    actor: Actor

    @model_validator(mode="after")
    def observation_requires_evidence(self) -> Self:
        if self.kind == "observation" and len(self.evidence_ids) < 1:
            raise ValueError("observation requires at least one evidence id")
        return self


class DispositionCreate(APIModel):
    action: DispositionAction
    rationale: str = Field(min_length=1)
    finding_ids: list[int] = Field(min_length=1)
    actor: Actor


class DecisionSource(APIModel):
    channel: Literal["grok_bot_chat"]
    chat_ref: str = Field(min_length=1, max_length=200)
    message_ref: str | None = Field(default=None, max_length=200)
    instruction_text: str = Field(min_length=1, max_length=2000)
    relayed_by: str = Field(min_length=1, max_length=100)


class DecisionCreate(APIModel):
    decision: DecisionValue
    rationale: str = Field(min_length=1)
    actor: Actor
    source: DecisionSource | None = None


class SoarSubmitRequest(APIModel):
    actor: Actor


class Case(APIModel):
    id: int
    alert_id: str
    title: str
    summary: str | None
    splunk_severity: Severity
    triage_severity: Severity | None
    triage_rationale: str | None
    triage_model: str | None
    assigned_analyst: str | None
    status: CaseStatus
    created_at: str
    updated_at: str
    alert_time: str | None = None
    model_requested: str | None = None
    route: Route | None = None
    rule: str | None = None
    verdict: Verdict | None = None
    recommended_action: RecommendedAction | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    privileged_account: bool | None = None
    crown_jewel_asset: bool | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class CaseSummary(APIModel):
    id: int
    alert_id: str
    title: str
    severity: Severity
    splunk_severity: Severity
    status: CaseStatus
    assigned_analyst: str | None
    created_at: str
    updated_at: str


class CaseList(APIModel):
    items: list[CaseSummary]
    total: int = Field(ge=0)


class Evidence(APIModel):
    id: int
    case_id: int
    source: str
    ref: str
    content: dict[str, Any]
    added_by: str
    created_at: str


class Finding(APIModel):
    id: int
    case_id: int
    kind: FindingKind
    statement: str
    evidence_ids: list[int]
    confidence: float | None
    author_id: str
    author_type: ActorType
    created_at: str


class Disposition(APIModel):
    id: int
    case_id: int
    action: DispositionAction
    rationale: str
    finding_ids: list[int]
    proposed_by: str
    created_at: str
    superseded: bool


class Decision(APIModel):
    id: int
    case_id: int
    disposition_id: int
    decision: DecisionValue
    rationale: str
    analyst_id: str
    created_at: str


class SoarSubmission(APIModel):
    id: int
    case_id: int
    disposition_id: int
    soar_case_id: str
    submitted_by: str
    created_at: str


class AuditEvent(APIModel):
    id: int
    case_id: int | None
    ts: str
    type: str
    actor: Actor
    component: str
    from_status: CaseStatus | None
    to_status: CaseStatus | None
    payload: dict[str, Any]


class CaseView(APIModel):
    id: int
    alert_id: str
    created_at: str
    updated_at: str
    title: str
    severity: Severity
    splunk_severity: Severity
    status: CaseStatus
    summary: str | None
    assigned_analyst: str | None
    evidence: list[Evidence]
    findings: list[Finding]
    proposed_disposition: Disposition | None
    decision: Decision | None
    soar: SoarSubmission | None
    audit: list[AuditEvent]
    alert_time: str | None = None
    model_requested: str | None = None
    route: Route | None = None
    rule: str | None = None
    verdict: Verdict | None = None
    recommended_action: RecommendedAction | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    privileged_account: bool | None = None
    crown_jewel_asset: bool | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class EventPage(APIModel):
    items: list[AuditEvent]
    next_after_id: int = Field(ge=0)


class ErrorResponse(APIModel):
    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(APIModel):
    status: Literal["ok"]
