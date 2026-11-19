"""Mock SOAR business rules: approval re-check and idempotency."""

from contracts.mock_soar import SoarReceipt, SoarRecord, SoarSubmissionRequest
from contracts.models import Actor
from services.mock_soar.errors import ApprovalRequired, ConflictingResubmission, SubmissionNotFound
from services.mock_soar.store import SoarStore, insert_submission


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class MockSoarService:
    def __init__(self, store: SoarStore) -> None:
        self._store = store

    def submit(self, body: SoarSubmissionRequest) -> tuple[SoarReceipt, int]:
        self._validate_approval(body)
        now = utc_now()
        with self._store.transaction() as conn:
            existing = self._store.get_by_case_id(conn, body.case_id)
            if existing is None:
                record, _ = insert_submission(conn, body, recorded_at=now)
                receipt = SoarReceipt(
                    soar_case_id=record.soar_case_id,
                    case_id=record.case_id,
                    created=True,
                    recorded_at=record.recorded_at,
                )
                return receipt, 201
            if (
                existing.disposition_id == body.disposition_id
                and existing.action == body.action
            ):
                receipt = SoarReceipt(
                    soar_case_id=existing.soar_case_id,
                    case_id=existing.case_id,
                    created=False,
                    recorded_at=existing.recorded_at,
                )
                return receipt, 200
            raise ConflictingResubmission(
                "case already submitted with a different disposition or action",
                details={
                    "case_id": body.case_id,
                    "soar_case_id": existing.soar_case_id,
                },
            )

    def get(self, soar_case_id: str) -> SoarRecord:
        with self._store.transaction() as conn:
            record = self._store.get_by_soar_id(conn, soar_case_id)
            if record is None:
                raise SubmissionNotFound(
                    "submission not found",
                    details={"soar_case_id": soar_case_id},
                )
            return record

    def list_for_case(self, case_id: int) -> list[SoarRecord]:
        with self._store.transaction() as conn:
            return self._store.list_by_case_id(conn, case_id)

    def reset(self) -> int:
        return self._store.reset()

    @staticmethod
    def _validate_approval(body: SoarSubmissionRequest) -> None:
        if body.approval.decision != "approve":
            raise ApprovalRequired(
                "submission requires an approved disposition",
                details={"decision": body.approval.decision},
            )
        if body.approval.approver.type != "analyst":
            raise ApprovalRequired(
                "approval must come from an analyst",
                details={"approver_type": body.approval.approver.type},
            )
        _ = Actor.model_validate(body.submitted_by.model_dump())