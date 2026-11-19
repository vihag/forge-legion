"""HTTP client for the Case API. No imports from services.case_api."""

from __future__ import annotations

from typing import Any, cast

import httpx

from contracts.models import Actor, DecisionSource


class CaseApiHttpError(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("message", "Case API error"))
        self.status = status
        self.body = body

    def as_tool_payload(self) -> dict[str, Any]:
        error = self.body.get("error", "error")
        message = self.body.get("message", "")
        details = self.body.get("details", {})
        if not isinstance(error, str):
            error = "error"
        if not isinstance(message, str):
            message = ""
        if not isinstance(details, dict):
            details = {}
        return {
            "status": self.status,
            "error": error,
            "message": message,
            "details": details,
        }


class CaseApiUnreachable(Exception):
    """The Case API transport failed."""


def mcp_actor(actor_id: str) -> Actor:
    return Actor(id=f"mcp:{actor_id}", type="agent")


class CaseApiClient:
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

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        url = f"{self._base_url}{path}"
        client = httpx.Client(transport=self._transport, timeout=self._timeout)
        try:
            try:
                response = client.request(method, url, params=params, json=json)
            except httpx.HTTPError as exc:
                raise CaseApiUnreachable from exc
            try:
                body: object = response.json()
            except Exception:
                body = {"error": "error", "message": response.text, "details": {}}
            if response.status_code >= 400:
                if not isinstance(body, dict):
                    body = {"error": "error", "message": "invalid response", "details": {}}
                raise CaseApiHttpError(response.status_code, body)
            if isinstance(body, list):
                return body
            if not isinstance(body, dict):
                body = {"error": "error", "message": "invalid response", "details": {}}
            return cast(dict[str, Any], body)
        finally:
            if self._transport is None:
                client.close()

    def _request_dict(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._request(method, path, params=params, json=json)
        if isinstance(result, dict):
            return result
        raise CaseApiHttpError(
            500,
            {"error": "error", "message": "invalid response", "details": {}},
        )

    def list_cases(
        self,
        *,
        status: str | None,
        severity: str | None,
        alert_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if severity is not None:
            params["severity"] = severity
        if alert_id is not None:
            params["alert_id"] = alert_id
        return self._request_dict("GET", "/v1/cases", params=params)

    def get_case(
        self,
        case_id: int,
        *,
        include: list[str] | None = None,
        audit_limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if include:
            params["include"] = ",".join(include)
        if audit_limit is not None:
            params["audit_limit"] = audit_limit
        if params:
            return self._request_dict("GET", f"/v1/cases/{case_id}", params=params)
        return self._request_dict("GET", f"/v1/cases/{case_id}/view")

    def create_incident_ticket(
        self,
        case_id: int,
        *,
        actor: Actor,
        summary: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"actor": actor.model_dump(mode="json")}
        if summary is not None:
            body["summary"] = summary
        return self._request_dict("POST", f"/v1/cases/{case_id}/incident-ticket", json=body)

    def get_case_history(self, case_id: int) -> list[Any]:
        result = self._request("GET", f"/v1/cases/{case_id}/history")
        if not isinstance(result, list):
            return []
        return result

    def add_evidence(
        self,
        case_id: int,
        *,
        source: str,
        ref: str,
        content: dict[str, Any],
        actor: Actor,
    ) -> dict[str, Any]:
        return self._request_dict(
            "POST",
            f"/v1/cases/{case_id}/evidence",
            json={
                "source": source,
                "ref": ref,
                "content": content,
                "actor": actor.model_dump(mode="json"),
            },
        )

    def record_finding(
        self,
        case_id: int,
        *,
        kind: str,
        statement: str,
        evidence_ids: list[int],
        confidence: float | None,
        actor: Actor,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": kind,
            "statement": statement,
            "evidence_ids": evidence_ids,
            "actor": actor.model_dump(mode="json"),
        }
        if confidence is not None:
            payload["confidence"] = confidence
        return self._request_dict("POST", f"/v1/cases/{case_id}/findings", json=payload)

    def propose_disposition(
        self,
        case_id: int,
        *,
        action: str,
        rationale: str,
        finding_ids: list[int],
        actor: Actor,
    ) -> dict[str, Any]:
        return self._request_dict(
            "POST",
            f"/v1/cases/{case_id}/disposition",
            json={
                "action": action,
                "rationale": rationale,
                "finding_ids": finding_ids,
                "actor": actor.model_dump(mode="json"),
            },
        )

    def request_soar_action(self, case_id: int, *, actor: Actor) -> dict[str, Any]:
        return self._request_dict(
            "POST",
            f"/v1/cases/{case_id}/soar-submission",
            json={"actor": actor.model_dump(mode="json")},
        )

    def record_decision(
        self,
        case_id: int,
        *,
        decision: str,
        rationale: str,
        actor: Actor,
        source: DecisionSource | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision": decision,
            "rationale": rationale,
            "actor": actor.model_dump(mode="json"),
        }
        if source is not None:
            payload["source"] = source.model_dump(mode="json")
        return self._request_dict("POST", f"/v1/cases/{case_id}/decision", json=payload)