"""Pydantic models for Mock SOAR (/v1)."""

from pydantic import Field

from contracts.models import Actor, APIModel, DecisionValue, DispositionAction


class SoarApproval(APIModel):
    decision_id: int
    decision: DecisionValue
    approver: Actor
    decided_at: str


class SoarSubmissionRequest(APIModel):
    case_id: int
    alert_id: str = Field(min_length=1)
    disposition_id: int
    action: DispositionAction
    approval: SoarApproval
    submitted_by: Actor


class SoarReceipt(APIModel):
    soar_case_id: str
    case_id: int
    created: bool
    recorded_at: str


class SoarRecord(APIModel):
    soar_case_id: str
    case_id: int
    alert_id: str
    disposition_id: int
    action: DispositionAction
    approval: SoarApproval
    submitted_by: Actor
    recorded_at: str


class SoarRecordList(APIModel):
    items: list[SoarRecord]


class SoarResetResponse(APIModel):
    submissions: int = Field(ge=0)