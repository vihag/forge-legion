"""Errors raised by Mock Splunk. Routes map the HTTP ones; fixture errors abort startup."""

from typing import Any


class SplunkError(Exception):
    """Base error. ``error`` and ``status_code`` are the API mapping."""

    status_code: int = 500
    error: str = "error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = {} if details is None else details


class AlertNotFound(SplunkError):
    status_code = 404
    error = "alert_not_found"


class UnknownSource(SplunkError):
    status_code = 422
    error = "unknown_source"


class InvalidWindow(SplunkError):
    status_code = 422
    error = "invalid_window"


class FixtureError(Exception):
    """Seed fixtures are invalid. Raised while loading, not mapped to HTTP."""
