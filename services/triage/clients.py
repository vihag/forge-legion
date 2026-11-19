"""HTTP clients for Case API and Mock Splunk."""

from typing import Any, Protocol, cast

import httpx

from contracts.ingest_mapping import alert_to_case_create, evidence_to_evidence_create
from contracts.mock_splunk import Alert, AlertPage, EvidenceList, SplunkEvidence
from contracts.models import (
    Actor,
    AutoResolveCreate,
    Case,
    CaseList,
    CaseView,
    Evidence,
    EvidenceCreate,
    ReviewFlagCreate,
    TriageCreate,
)


class Transport(Protocol):
    def handle_request(self, request: httpx.Request) -> httpx.Response: ...


class CaseApiClient:
    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(base_url=base_url, transport=transport, timeout=30.0)

    def close(self) -> None:
        self._client.close()

    def create_case(self, body: dict[str, Any]) -> Case:
        response = self._client.post("/v1/cases", json=body)
        if response.status_code == 409:
            raise DuplicateCaseError(response.json())
        response.raise_for_status()
        return Case.model_validate(response.json())

    def list_cases_by_alert_id(self, alert_id: str) -> CaseList:
        response = self._client.get("/v1/cases", params={"alert_id": alert_id})
        response.raise_for_status()
        return CaseList.model_validate(response.json())

    def get_case(self, case_id: int) -> Case:
        response = self._client.get(f"/v1/cases/{case_id}")
        response.raise_for_status()
        return Case.model_validate(response.json())

    def list_evidence(self, case_id: int) -> list[Evidence]:
        response = self._client.get(f"/v1/cases/{case_id}/evidence")
        response.raise_for_status()
        data = response.json()
        return [Evidence.model_validate(item) for item in cast(list[Any], data)]

    def add_evidence(self, case_id: int, body: EvidenceCreate) -> Evidence:
        response = self._client.post(
            f"/v1/cases/{case_id}/evidence",
            json=body.model_dump(mode="json"),
        )
        response.raise_for_status()
        return Evidence.model_validate(response.json())

    def triage(self, case_id: int, body: TriageCreate) -> Case:
        response = self._client.post(
            f"/v1/cases/{case_id}/triage",
            json=body.model_dump(mode="json"),
        )
        response.raise_for_status()
        return Case.model_validate(response.json())

    def flag_review(self, case_id: int, body: ReviewFlagCreate) -> Case:
        response = self._client.post(
            f"/v1/cases/{case_id}/review-flag",
            json=body.model_dump(mode="json"),
        )
        response.raise_for_status()
        return Case.model_validate(response.json())

    def auto_resolve(self, case_id: int, body: AutoResolveCreate) -> Case:
        response = self._client.post(
            f"/v1/cases/{case_id}/auto-resolve",
            json=body.model_dump(mode="json"),
        )
        if response.status_code == 409:
            raise AutoResolveRejected(response.json())
        response.raise_for_status()
        return Case.model_validate(response.json())

    def get_case_view(self, case_id: int) -> CaseView:
        response = self._client.get(f"/v1/cases/{case_id}/view")
        response.raise_for_status()
        return CaseView.model_validate(response.json())


class SplunkClient:
    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(base_url=base_url, transport=transport, timeout=30.0)

    def close(self) -> None:
        self._client.close()

    def get_alert(self, alert_id: str) -> Alert:
        response = self._client.get(f"/v1/alerts/{alert_id}")
        if response.status_code == 404:
            raise AlertNotFound(alert_id)
        response.raise_for_status()
        return Alert.model_validate(response.json())

    def list_evidence(self, alert_id: str) -> EvidenceList:
        response = self._client.get(f"/v1/alerts/{alert_id}/evidence")
        response.raise_for_status()
        return EvidenceList.model_validate(response.json())

    def reset(self) -> None:
        response = self._client.post("/v1/reset", json={})
        response.raise_for_status()

    def list_alerts(
        self,
        *,
        after_seq: int = 0,
        limit: int = 100,
    ) -> AlertPage:
        response = self._client.get(
            "/v1/alerts",
            params={"after_seq": after_seq, "limit": limit},
        )
        response.raise_for_status()
        return AlertPage.model_validate(response.json())


class AlertNotFound(Exception):
    def __init__(self, alert_id: str) -> None:
        super().__init__(f"alert not found: {alert_id}")
        self.alert_id = alert_id


class DuplicateCaseError(Exception):
    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__("duplicate alert_id")
        self.body = body


class AutoResolveRejected(Exception):
    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__("auto-resolve rejected")
        self.body = body


TRIAGE_ACTOR = Actor(id="triage-langgraph", type="system")
AUTO_RESOLVE_ACTOR = Actor(id="policy:autonomous-triage", type="system")


def ingest_from_alert(case_client: CaseApiClient, alert: Alert) -> Case:
    create = alert_to_case_create(alert, TRIAGE_ACTOR)
    return case_client.create_case(create.model_dump(mode="json"))


def attach_splunk_evidence(
    case_client: CaseApiClient,
    case_id: int,
    splunk_item: SplunkEvidence,
) -> Evidence:
    create = evidence_to_evidence_create(splunk_item, TRIAGE_ACTOR)
    return case_client.add_evidence(case_id, create)