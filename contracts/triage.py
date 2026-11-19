"""Shared models for the LangGraph triage service and Grok contract."""

from typing import Literal

from pydantic import Field

from contracts.mock_splunk import MitreTechnique
from contracts.models import (
    APIModel,
    RecommendedAction,
    ReviewFlagCreate,
    ReviewFlagReason,
    Severity,
    Verdict,
)

Route = Literal["collaborative", "autonomous", "review"]
TriageOutcome = Literal["triaged", "needs_review", "skipped_existing"]


class GrokAssessment(APIModel):
    original_severity: Severity
    assessed_severity: Severity
    assessment_confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence_references: list[str] = Field(min_length=1)
    mitre_techniques: list[MitreTechnique] = Field(default_factory=list)
    recommended_route: Route
    additional_evidence_needed: list[str] = Field(default_factory=list)
    verdict: Verdict | None = None
    recommended_action: RecommendedAction | None = None

class TriageResult(APIModel):
    alert_id: str
    case_id: int | None = None
    outcome: TriageOutcome
    route: Route | None = None
    rule: str | None = None
    assessed_severity: Severity | None = None
    model_requested: str
    model_returned: str | None = None
    response_id: str | None = None


__all__ = [
    "GrokAssessment",
    "ReviewFlagCreate",
    "ReviewFlagReason",
    "Route",
    "TriageOutcome",
    "TriageResult",
]