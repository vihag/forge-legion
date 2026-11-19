"""The transition table allows only the moves in contract §2."""

import pytest

from contracts.models import CaseStatus
from services.case_api.state import TRANSITIONS, TransitionDenied, check_transition

STATUSES = [
    "NEW",
    "TRIAGED",
    "INVESTIGATING",
    "DISPOSITION_PROPOSED",
    "APPROVED",
    "REJECTED",
    "SUBMITTED",
]

OPERATIONS = [
    "ingest",
    "triage",
    "add_evidence",
    "record_finding",
    "propose_disposition",
    "approve",
    "reject",
    "submit_to_soar",
]

# Resulting status for every allowed (operation, from_status) pair in §2.
# add_evidence leaves the status unchanged.
DOCUMENTED: dict[tuple[str, str], str] = {
    ("triage", "NEW"): "TRIAGED",
    ("triage", "TRIAGED"): "TRIAGED",
    ("add_evidence", "NEW"): "NEW",
    ("add_evidence", "TRIAGED"): "TRIAGED",
    ("add_evidence", "INVESTIGATING"): "INVESTIGATING",
    ("add_evidence", "REJECTED"): "REJECTED",
    ("record_finding", "TRIAGED"): "INVESTIGATING",
    ("record_finding", "INVESTIGATING"): "INVESTIGATING",
    ("record_finding", "REJECTED"): "INVESTIGATING",
    ("propose_disposition", "INVESTIGATING"): "DISPOSITION_PROPOSED",
    ("approve", "DISPOSITION_PROPOSED"): "APPROVED",
    ("reject", "DISPOSITION_PROPOSED"): "REJECTED",
    ("submit_to_soar", "APPROVED"): "SUBMITTED",
}


def _documented_from(operation: str) -> list[str]:
    return [status for (op, status) in DOCUMENTED if op == operation]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status", STATUSES)
def test_transition_table_allows_only_documented_moves(operation: str, status: str) -> None:
    assert [item.value for item in CaseStatus] == STATUSES
    assert set(TRANSITIONS) == set(OPERATIONS) - {"ingest"}
    key = (operation, status)
    if key in DOCUMENTED:
        expected = DOCUMENTED[key]
        assert expected in STATUSES
        raw = TRANSITIONS[operation][status]
        assert (status if raw is None else raw) == expected
        assert check_transition(operation, status) == expected
        return
    with pytest.raises(TransitionDenied) as exc_info:
        check_transition(operation, status)
    denied = exc_info.value
    assert denied.operation == operation
    assert denied.from_status == status
    assert denied.allowed_from == _documented_from(operation)
    if operation in TRANSITIONS:
        assert status not in TRANSITIONS[operation]
    else:
        assert operation not in TRANSITIONS
