"""Errors raised by Mock SOAR."""

from typing import Any


class MockSoarError(Exception):
    status_code: int = 500
    error: str = "error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = {} if details is None else details


class SubmissionNotFound(MockSoarError):
    status_code = 404
    error = "not_found"


class ConflictingResubmission(MockSoarError):
    status_code = 409
    error = "conflicting_resubmission"


class ApprovalRequired(MockSoarError):
    status_code = 422
    error = "approval_required"


class InvalidQuery(MockSoarError):
    status_code = 422
    error = "validation_error"