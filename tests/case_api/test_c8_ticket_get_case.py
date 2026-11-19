"""C8 incident ticket and get_case include (T1, T2, G1, G2)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from services.case_api.app import create_app
from services.mcp_adapter.case_client import CaseApiClient, mcp_actor
from services.triage.http_transport import InProcessTransport
from tests.case_api.support import (
    DISPOSITION_ACTOR,
    add_evidence,
    add_finding,
    decide,
    ingest,
    propose,
    triage,
)


def _approved_case(client: TestClient) -> int:
    case = ingest(client, "ALT-C8")
    case_id = case["id"]
    triage(client, case_id)
    evidence = add_evidence(client, case_id)
    finding = add_finding(client, case_id, evidence_ids=[evidence["id"]])
    propose(client, case_id, finding_ids=[finding["id"]])
    decide(client, case_id)
    return case_id


def test_t1_ticket_idempotent(client: TestClient) -> None:
    case_id = _approved_case(client)
    first = client.post(
        f"/v1/cases/{case_id}/incident-ticket",
        json={"actor": DISPOSITION_ACTOR},
    )
    assert first.status_code == 201
    body = first.json()
    assert body["ticket_id"] == f"INC-{case_id}"
    assert body["created"] is True
    history = client.get(f"/v1/cases/{case_id}/history").json()
    assert sum(1 for event in history if event["type"] == "soar.ticket_created") == 1
    second = client.post(
        f"/v1/cases/{case_id}/incident-ticket",
        json={"actor": DISPOSITION_ACTOR},
    )
    assert second.status_code == 200
    assert second.json()["created"] is False
    history2 = client.get(f"/v1/cases/{case_id}/history").json()
    assert sum(1 for event in history2 if event["type"] == "soar.ticket_created") == 1


def test_t2_ticket_guards(client: TestClient) -> None:
    case = ingest(client, "ALT-C8B")
    case_id = case["id"]
    triage(client, case_id)
    denied = client.post(
        f"/v1/cases/{case_id}/incident-ticket",
        json={"actor": DISPOSITION_ACTOR},
    )
    assert denied.status_code == 409
    assert denied.json()["error"] == "no_analyst_approval"
    forbidden = client.post(
        f"/v1/cases/{case_id}/incident-ticket",
        json={"actor": {"id": "auto", "type": "system"}},
    )
    assert forbidden.status_code == 403


def test_g1_get_case_default_uses_view_and_include_is_case_based(client: TestClient) -> None:
    case_id = _approved_case(client)
    view = client.get(f"/v1/cases/{case_id}/view").json()
    transport = InProcessTransport(client.app)
    api = CaseApiClient("http://case.test", transport=transport)
    assert api.get_case(case_id) == view
    slim = client.get(f"/v1/cases/{case_id}", params={"include": "audit", "audit_limit": 3}).json()
    case_only = client.get(f"/v1/cases/{case_id}").json()
    assert set(slim.keys()) - set(case_only.keys()) == {"audit"}
    audit = client.get(f"/v1/cases/{case_id}", params={"include": "audit", "audit_limit": 3})
    assert audit.status_code == 200
    assert len(audit.json()["audit"]) <= 3
    bogus = client.get(f"/v1/cases/{case_id}", params={"include": "bogus"})
    assert bogus.status_code == 422
    bad_limit = client.get(
        f"/v1/cases/{case_id}",
        params={"include": "audit", "audit_limit": 0},
    )
    assert bad_limit.status_code == 422


def test_g2_mcp_pass_through(tmp_path: Path) -> None:
    case_app = create_app(db_path=str(tmp_path / "cases.db"))
    transport = InProcessTransport(case_app)
    client = CaseApiClient("http://case.test", transport=transport)
    http = TestClient(case_app)
    case_id = _approved_case(http)
    payload = client.get_case(case_id, include=["audit"], audit_limit=5)
    assert "audit" in payload
    ticket = client.create_incident_ticket(
        case_id,
        actor=mcp_actor("disposition-sentinel"),
    )
    assert ticket["ticket_id"] == f"INC-{case_id}"