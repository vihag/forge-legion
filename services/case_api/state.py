"""Pure case-status transition table.

A target of None means the operation is allowed and the status does not change.
This module does no I/O.
"""

TRIAGE = "triage"
ADD_EVIDENCE = "add_evidence"
RECORD_FINDING = "record_finding"
PROPOSE_DISPOSITION = "propose_disposition"
APPROVE = "approve"
REJECT = "reject"
SUBMIT_TO_SOAR = "submit_to_soar"

# operation -> from_status -> to_status. None keeps the current status.
TRANSITIONS: dict[str, dict[str, str | None]] = {
    TRIAGE: {
        "NEW": "TRIAGED",
        "TRIAGED": "TRIAGED",
    },
    ADD_EVIDENCE: {
        "NEW": None,
        "TRIAGED": None,
        "INVESTIGATING": None,
        "REJECTED": None,
    },
    RECORD_FINDING: {
        "TRIAGED": "INVESTIGATING",
        "INVESTIGATING": "INVESTIGATING",
        "REJECTED": "INVESTIGATING",
    },
    PROPOSE_DISPOSITION: {
        "INVESTIGATING": "DISPOSITION_PROPOSED",
    },
    APPROVE: {
        "DISPOSITION_PROPOSED": "APPROVED",
    },
    REJECT: {
        "DISPOSITION_PROPOSED": "REJECTED",
    },
    SUBMIT_TO_SOAR: {
        "APPROVED": "SUBMITTED",
    },
}


class TransitionDenied(Exception):
    """Raised when an operation is not allowed from the current status."""

    def __init__(self, operation: str, from_status: str, allowed_from: list[str]) -> None:
        self.operation = operation
        self.from_status = from_status
        self.allowed_from = allowed_from
        allowed = ", ".join(allowed_from) if allowed_from else "(none)"
        super().__init__(f"{operation} is not allowed from {from_status}; allowed from {allowed}")


def check_transition(operation: str, from_status: str) -> str:
    """Return the status after `operation`.

    Raises TransitionDenied when the pair is not in the table.
    """
    targets = TRANSITIONS.get(operation)
    if targets is None or from_status not in targets:
        allowed = list(targets) if targets is not None else []
        raise TransitionDenied(operation, from_status, allowed)
    target = targets[from_status]
    if target is None:
        return from_status
    return target
