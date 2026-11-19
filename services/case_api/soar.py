"""SOAR client boundary. C1 ships a deterministic fake; Mock SOAR plugs in via HttpSoarClient."""

from typing import Protocol

import httpx

from contracts.mock_soar import (
    SoarReceipt,
    SoarSubmissionRequest,
    SoarTicketReceipt,
    SoarTicketRequest,
)


class SoarError(Exception):
    """Raised by a SoarClient when the submission cannot be completed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SoarClient(Protocol):
    """Submit an approved case payload. Return the SOAR case id, or raise SoarError."""

    def submit(self, request: SoarSubmissionRequest) -> str:
        """Return the SOAR case id for the submission."""

    def create_ticket(self, request: SoarTicketRequest) -> SoarTicketReceipt:
        """Create or return an incident ticket."""


class FakeSoarClient:
    """Deterministic stand-in. `submit` returns `SOAR-<case_id>-0001`."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self._tickets: dict[int, SoarTicketReceipt] = {}

    def submit(self, request: SoarSubmissionRequest) -> str:
        if self.fail:
            raise SoarError("SOAR request failed")
        return f"SOAR-{request.case_id}-0001"

    def create_ticket(self, request: SoarTicketRequest) -> SoarTicketReceipt:
        if self.fail:
            raise SoarError("SOAR request failed")
        existing = self._tickets.get(request.case_id)
        if existing is not None:
            return SoarTicketReceipt(
                ticket_id=existing.ticket_id,
                case_id=existing.case_id,
                created=False,
                recorded_at=existing.recorded_at,
            )
        receipt = SoarTicketReceipt(
            ticket_id=f"INC-{request.case_id}",
            case_id=request.case_id,
            created=True,
            recorded_at="2026-01-01T00:00:00Z",
        )
        self._tickets[request.case_id] = receipt
        return receipt


class HttpSoarClient:
    """POST the submission to Mock SOAR (/v1/submissions)."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    def submit(self, request: SoarSubmissionRequest) -> str:
        url = f"{self._base_url}/v1/submissions"
        client = httpx.Client(transport=self._transport, timeout=self._timeout)
        try:
            try:
                response = client.post(url, json=request.model_dump(mode="json"))
            except httpx.HTTPError as exc:
                raise SoarError(f"SOAR transport error: {exc}") from exc
            if response.status_code not in (200, 201):
                detail = _error_message(response)
                raise SoarError(detail)
            try:
                receipt = SoarReceipt.model_validate(response.json())
            except Exception as exc:
                raise SoarError("invalid SOAR response") from exc
            return receipt.soar_case_id
        finally:
            if self._transport is None:
                client.close()

    def create_ticket(self, request: SoarTicketRequest) -> SoarTicketReceipt:
        url = f"{self._base_url}/v1/tickets"
        client = httpx.Client(transport=self._transport, timeout=self._timeout)
        try:
            try:
                response = client.post(url, json=request.model_dump(mode="json"))
            except httpx.HTTPError as exc:
                raise SoarError(f"SOAR transport error: {exc}") from exc
            if response.status_code not in (200, 201):
                detail = _error_message(response)
                raise SoarError(detail)
            try:
                return SoarTicketReceipt.model_validate(response.json())
            except Exception as exc:
                raise SoarError("invalid SOAR ticket response") from exc
        finally:
            if self._transport is None:
                client.close()


def _error_message(response: httpx.Response) -> str:
    try:
        body: object = response.json()
        if isinstance(body, dict):
            message = body.get("message")
            if isinstance(message, str):
                return message
    except Exception:
        pass
    return f"SOAR HTTP {response.status_code}"