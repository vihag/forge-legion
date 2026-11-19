"""Input shapes for MCP adapter tools (contract documentation and validation)."""

from typing import Any

from pydantic import Field, field_validator

from contracts.models import APIModel, CaseStatus, DispositionAction, FindingKind, Severity

MCP_ACTOR_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,59}$"


class ListCasesInput(APIModel):
    status: CaseStatus | None = None
    severity: Severity | None = None
    alert_id: str | None = Field(default=None, min_length=1)
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class CaseIdInput(APIModel):
    case_id: int = Field(ge=1)


class GetCaseInput(APIModel):
    case_id: int = Field(ge=1)
    include: list[str] | None = None
    audit_limit: int | None = Field(default=None, ge=1, le=200)


class CreateIncidentTicketInput(APIModel):
    case_id: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=60, pattern=MCP_ACTOR_ID_PATTERN)
    summary: str | None = None


class AddEvidenceInput(APIModel):
    case_id: int = Field(ge=1)
    source: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    content: dict[str, Any]
    actor_id: str = Field(min_length=1, max_length=60, pattern=MCP_ACTOR_ID_PATTERN)


class RecordFindingInput(APIModel):
    case_id: int = Field(ge=1)
    kind: FindingKind
    statement: str = Field(min_length=1)
    evidence_ids: list[int] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    actor_id: str = Field(min_length=1, max_length=60, pattern=MCP_ACTOR_ID_PATTERN)


class ProposeDispositionInput(APIModel):
    case_id: int = Field(ge=1)
    action: DispositionAction
    rationale: str = Field(min_length=1)
    finding_ids: list[int] = Field(min_length=1)
    actor_id: str = Field(min_length=1, max_length=60, pattern=MCP_ACTOR_ID_PATTERN)


class RequestSoarInput(APIModel):
    case_id: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=60, pattern=MCP_ACTOR_ID_PATTERN)


class MutatingToolInput(APIModel):
    actor_id: str = Field(min_length=1, max_length=60, pattern=MCP_ACTOR_ID_PATTERN)

    @field_validator("actor_id")
    @classmethod
    def actor_id_pattern(cls, value: str) -> str:
        import re

        if not re.fullmatch(MCP_ACTOR_ID_PATTERN, value):
            raise ValueError("actor_id must match ^[a-z0-9][a-z0-9-]{0,59}$")
        return value