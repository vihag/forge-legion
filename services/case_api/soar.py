"""SOAR client boundary. C1 ships a deterministic fake; a later task plugs in the real client."""

from typing import Protocol


class SoarError(Exception):
    """Raised by a SoarClient when the submission cannot be completed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SoarClient(Protocol):
    """Submit an approved case. Return the SOAR case id, or raise SoarError."""

    def submit(self, case_id: int) -> str:
        """Return the SOAR case id for `case_id`."""


class FakeSoarClient:
    """Deterministic stand-in. `submit` returns `SOAR-<case_id>-0001`."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def submit(self, case_id: int) -> str:
        if self.fail:
            raise SoarError("SOAR request failed")
        return f"SOAR-{case_id}-0001"
