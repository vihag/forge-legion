"""Domain errors raised by the case service. Routes map these to HTTP."""

from typing import Any


class CaseError(Exception):
    """Base error. `error` and `status_code` are the API mapping."""

    status_code: int = 500
    error: str = "error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = {} if details is None else details


class NotFound(CaseError):
    status_code = 404
    error = "not_found"


class IllegalTransition(CaseError):
    status_code = 409
    error = "illegal_transition"


class Forbidden(CaseError):
    status_code = 403
    error = "forbidden"


class Duplicate(CaseError):
    status_code = 409
    error = "duplicate"


class Conflict(CaseError):
    status_code = 409
    error = "conflict"


class PolicyGateFailed(CaseError):
    status_code = 409
    error = "policy_gate_failed"


class NoAnalystApproval(CaseError):
    status_code = 409
    error = "no_analyst_approval"


class DomainValidationError(CaseError):
    status_code = 422
    error = "validation_error"


class InstructionMismatch(CaseError):
    status_code = 422
    error = "instruction_mismatch"


class SoarUnavailable(CaseError):
    status_code = 502
    error = "soar_unavailable"
