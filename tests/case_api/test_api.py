"""HTTP acceptance checks for transitions, analyst approval, and request validation."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.case_api.app import create_app
from services.case_api.soar import FakeSoarClient
from services.case_api.store import Store

from .support import (
    AGENT,
    ANALYST,
    SYSTEM,
    add_evidence,
    add_finding,
    all_events,
    decide,
    ingest,
    propose,
    submit,
    triage,
    walk_to_proposed,
)


def test_record_finding_on_new_case_returns_409(client: TestClient) -> None:
    case = ingest(client, "ALT-NEW-FINDING")
    response = client.post(
        f"/v1/cases/{case['id']}/findings",
        json={
            "kind": "hypothesis",
            "statement": "too early",
            "evidence_ids": [],
            "actor": AGENT,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "illegal_transition"
    assert client.get(f"/v1/cases/{case['id']}").json()["status"] == "NEW"


def test_propose_without_findings_returns_409(
    client: TestClient,
    seeded_case: dict[str, object],
) -> None:
    case_id = seeded_case["case_id"]
    response = client.post(
        f"/v1/cases/{case_id}/disposition",
        json={
            "action": "contain",
            "rationale": "nothing to cite",
            "finding_ids": [1],
            "actor": AGENT,
        },
    )
    assert response.status_code == 409
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "TRIAGED"


def test_decision_when_not_proposed_returns_409(client: TestClient) -> None:
    case = ingest(client, "ALT-EARLY-DECISION")
    triage(client, case["id"])
    response = client.post(
        f"/v1/cases/{case['id']}/decision",
        json={"decision": "approve", "rationale": "too soon", "actor": ANALYST},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "illegal_transition"
    assert body["details"]["from_status"] == "TRIAGED"
    assert client.get(f"/v1/cases/{case['id']}").json()["status"] == "TRIAGED"


def test_submit_before_approval_returns_409(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-EARLY-SOAR")
    response = client.post(
        f"/v1/cases/{proposed['case_id']}/soar-submission",
        json={"actor": SYSTEM},
    )
    assert response.status_code == 409
    assert client.get(f"/v1/cases/{proposed['case_id']}").json()["status"] == "DISPOSITION_PROPOSED"


def test_submit_twice_returns_409_and_single_submission_row(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-TWICE")
    case_id = proposed["case_id"]
    decide(client, case_id)
    first = submit(client, case_id)
    before = all_events(client)
    second = client.post(
        f"/v1/cases/{case_id}/soar-submission",
        json={"actor": {"id": "soar-bot", "type": "system"}},
    )
    assert second.status_code == 409
    assert all_events(client) == before
    store: Store = client.app.state.store
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM soar_submissions WHERE case_id = ?",
            (case_id,),
        ).fetchone()
    assert row is not None
    assert int(row["n"]) == 1
    assert first["soar_case_id"] == f"SOAR-{case_id}-0001"
    view = client.get(f"/v1/cases/{case_id}/view").json()
    assert view["soar"]["soar_case_id"] == first["soar_case_id"]


def test_illegal_transition_body_names_from_status_and_allowed(client: TestClient) -> None:
    case = ingest(client, "ALT-BODY")
    response = client.post(
        f"/v1/cases/{case['id']}/findings",
        json={
            "kind": "hypothesis",
            "statement": "too early",
            "evidence_ids": [],
            "actor": AGENT,
        },
    )
    assert response.status_code == 409
    body = response.json()
    assert set(body) == {"error", "message", "details"}
    assert body["error"] == "illegal_transition"
    assert body["details"]["operation"] == "record_finding"
    assert body["details"]["from_status"] == "NEW"
    assert body["details"]["allowed_from"] == ["TRIAGED", "INVESTIGATING", "REJECTED"]


def test_approve_by_agent_actor_returns_403(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-AGENT-DECIDE")
    case_id = proposed["case_id"]
    before = all_events(client)
    response = client.post(
        f"/v1/cases/{case_id}/decision",
        json={"decision": "approve", "rationale": "a bot said so", "actor": AGENT},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert all_events(client) == before
    view = client.get(f"/v1/cases/{case_id}/view").json()
    assert view["status"] == "DISPOSITION_PROPOSED"
    assert view["decision"] is None


def test_decision_without_rationale_returns_422(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-RATIONALE")
    case_id = proposed["case_id"]
    empty = client.post(
        f"/v1/cases/{case_id}/decision",
        json={"decision": "approve", "rationale": "", "actor": ANALYST},
    )
    assert empty.status_code == 422
    assert empty.json()["error"] == "validation_error"
    assert set(empty.json()) == {"error", "message", "details"}
    missing = client.post(
        f"/v1/cases/{case_id}/decision",
        json={"decision": "approve", "actor": ANALYST},
    )
    assert missing.status_code == 422
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "DISPOSITION_PROPOSED"


def test_proposal_alone_does_not_approve(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-PROPOSAL")
    case_id = proposed["case_id"]
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "DISPOSITION_PROPOSED"
    view = client.get(f"/v1/cases/{case_id}/view").json()
    assert view["decision"] is None
    assert view["proposed_disposition"]["action"] == "contain"
    response = client.post(
        f"/v1/cases/{case_id}/soar-submission",
        json={"actor": SYSTEM},
    )
    assert response.status_code == 409
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "DISPOSITION_PROPOSED"


def test_analyst_approve_then_submit_returns_soar_id(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-HAPPY")
    case_id = proposed["case_id"]
    decision = decide(client, case_id)
    assert decision["decision"] == "approve"
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "APPROVED"
    submission = submit(client, case_id)
    assert submission["soar_case_id"] == f"SOAR-{case_id}-0001"
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "SUBMITTED"


def test_reject_then_new_finding_requires_fresh_proposal_and_decision(
    client: TestClient,
) -> None:
    proposed = walk_to_proposed(client, "ALT-REJECT")
    case_id = proposed["case_id"]
    evidence_id = proposed["evidence"]["id"]
    first_disposition_id = proposed["disposition"]["id"]
    decide(client, case_id, decision="reject", rationale="need more evidence")
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "REJECTED"
    assert client.post(
        f"/v1/cases/{case_id}/soar-submission",
        json={"actor": SYSTEM},
    ).status_code == 409

    finding = add_finding(
        client,
        case_id,
        [evidence_id],
        statement="the host kept failing after the rejection",
    )
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "INVESTIGATING"
    assert client.post(
        f"/v1/cases/{case_id}/soar-submission",
        json={"actor": SYSTEM},
    ).status_code == 409
    rejected_again = client.post(
        f"/v1/cases/{case_id}/decision",
        json={
            "decision": "approve",
            "rationale": "the old rejection still stands",
            "actor": ANALYST,
        },
    )
    assert rejected_again.status_code == 409

    proposal = propose(client, case_id, [finding["id"]])
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "DISPOSITION_PROPOSED"
    view = client.get(f"/v1/cases/{case_id}/view").json()
    assert view["decision"] is None
    assert view["proposed_disposition"]["id"] == proposal["id"]
    assert proposal["id"] != first_disposition_id
    assert client.post(
        f"/v1/cases/{case_id}/soar-submission",
        json={"actor": SYSTEM},
    ).status_code == 409

    decide(client, case_id, rationale="the new finding is enough")
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "APPROVED"
    submission = submit(client, case_id)
    assert submission["soar_case_id"] == f"SOAR-{case_id}-0001"


def test_observation_without_evidence_returns_422(
    client: TestClient,
    seeded_case: dict[str, object],
) -> None:
    case_id = seeded_case["case_id"]
    before = all_events(client)
    response = client.post(
        f"/v1/cases/{case_id}/findings",
        json={
            "kind": "observation",
            "statement": "saw it",
            "evidence_ids": [],
            "actor": AGENT,
        },
    )
    assert response.status_code == 422
    assert "observation" in response.text
    assert all_events(client) == before
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "TRIAGED"


def test_finding_with_foreign_evidence_id_returns_422(client: TestClient) -> None:
    first = ingest(client, "ALT-HOME")
    second = ingest(client, "ALT-FOREIGN")
    triage(client, first["id"])
    triage(client, second["id"])
    foreign = add_evidence(client, second["id"])
    before = all_events(client, case_id=first["id"])
    response = client.post(
        f"/v1/cases/{first['id']}/findings",
        json={
            "kind": "observation",
            "statement": "uses another case's evidence",
            "evidence_ids": [foreign["id"]],
            "actor": AGENT,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert foreign["id"] in response.json()["details"]["evidence_ids"]
    assert all_events(client, case_id=first["id"]) == before
    assert client.get(f"/v1/cases/{first['id']}").json()["status"] == "TRIAGED"


def test_view_returns_typed_findings_with_evidence_ids(client: TestClient) -> None:
    case = ingest(client, "ALT-VIEW")
    case_id = case["id"]
    triage(client, case_id, severity="critical")
    evidence = add_evidence(client, case_id)
    finding = add_finding(
        client,
        case_id,
        [evidence["id"]],
        statement="forty failures from one host",
        confidence=0.75,
    )
    response = client.get(f"/v1/cases/{case_id}/view")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == case_id
    assert body["severity"] == "critical"
    assert body["splunk_severity"] == "high"
    assert body["status"] == "INVESTIGATING"
    assert body["proposed_disposition"] is None
    assert body["decision"] is None
    assert body["soar"] is None
    assert body["evidence"][0]["content"] == {"src": "10.0.0.8", "failures": 42}
    assert len(body["findings"]) == 1
    viewed = body["findings"][0]
    assert viewed["id"] == finding["id"]
    assert viewed["kind"] == "observation"
    assert viewed["evidence_ids"] == [evidence["id"]]
    assert viewed["confidence"] == 0.75
    assert [event["type"] for event in body["audit"]] == [
        "case.ingested",
        "case.triaged",
        "evidence.added",
        "finding.recorded",
    ]


def test_duplicate_alert_id_returns_409(client: TestClient) -> None:
    ingest(client, "ALT-DUP")
    response = client.post(
        "/v1/cases",
        json={
            "alert_id": "ALT-DUP",
            "title": "again",
            "splunk_severity": "low",
            "actor": SYSTEM,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "duplicate"
    assert client.get("/v1/cases").json()["total"] == 1


def test_missing_case_returns_404(client: TestClient) -> None:
    missing = 999
    for path in (
        f"/v1/cases/{missing}",
        f"/v1/cases/{missing}/evidence",
        f"/v1/cases/{missing}/history",
        f"/v1/cases/{missing}/view",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"] == "not_found"
    writes = [
        ("/triage", {"severity": "low", "rationale": "x", "model": "grok-3", "actor": AGENT}),
        ("/evidence", {"source": "mock_splunk", "ref": "r", "content": {}, "actor": AGENT}),
        (
            "/findings",
            {"kind": "hypothesis", "statement": "x", "evidence_ids": [], "actor": AGENT},
        ),
        (
            "/disposition",
            {"action": "benign", "rationale": "x", "finding_ids": [1], "actor": AGENT},
        ),
        ("/decision", {"decision": "approve", "rationale": "x", "actor": ANALYST}),
        ("/soar-submission", {"actor": SYSTEM}),
    ]
    for suffix, payload in writes:
        response = client.post(f"/v1/cases/{missing}{suffix}", json=payload)
        assert response.status_code == 404, suffix
    events = client.get("/v1/events", params={"case_id": missing})
    assert events.status_code == 200
    assert events.json()["items"] == []


def test_list_cases_filters_by_status_and_severity(client: TestClient) -> None:
    first = ingest(client, "ALT-LIST-1", splunk_severity="low")
    second = ingest(client, "ALT-LIST-2", splunk_severity="high")
    triage(client, second["id"], severity="critical")
    listed = client.get("/v1/cases")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [first["id"], second["id"]]
    assert body["items"][1]["severity"] == "critical"
    assert body["items"][0]["severity"] == "low"

    by_status = client.get("/v1/cases", params={"status": "TRIAGED"})
    assert by_status.json()["total"] == 1
    assert [item["id"] for item in by_status.json()["items"]] == [second["id"]]

    by_severity = client.get("/v1/cases", params={"severity": "critical"})
    assert [item["id"] for item in by_severity.json()["items"]] == [second["id"]]
    assert client.get("/v1/cases", params={"severity": "high"}).json()["total"] == 0
    low = client.get("/v1/cases", params={"severity": "low"}).json()["items"]
    assert [item["id"] for item in low] == [first["id"]]

    page = client.get("/v1/cases", params={"limit": 1, "offset": 1})
    assert page.json()["total"] == 2
    assert [item["id"] for item in page.json()["items"]] == [second["id"]]
    assert client.get("/v1/cases", params={"status": "NOPE"}).status_code == 422


def test_retriage_from_triaged_updates_assessment(
    client: TestClient,
    seeded_case: dict[str, object],
) -> None:
    case_id = seeded_case["case_id"]
    before = len(all_events(client))
    updated = triage(client, case_id, severity="low")
    assert updated["status"] == "TRIAGED"
    assert updated["triage_severity"] == "low"
    events = all_events(client)
    assert len(events) == before + 1
    assert events[-1]["type"] == "case.triaged"
    assert events[-1]["from_status"] == "TRIAGED"
    assert events[-1]["to_status"] == "TRIAGED"


def test_soar_failure_returns_502_and_leaves_state_unchanged(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cases.db")
    failing = create_app(db_path=db_path, soar_client=FakeSoarClient(fail=True))
    with TestClient(failing) as client:
        proposed = walk_to_proposed(client, "ALT-SOAR-FAIL")
        case_id = proposed["case_id"]
        decide(client, case_id)
        before = all_events(client)
        response = client.post(
            f"/v1/cases/{case_id}/soar-submission",
            json={"actor": SYSTEM},
        )
        assert response.status_code == 502
        assert response.json()["error"] == "soar_unavailable"
        assert all_events(client) == before
        assert client.get(f"/v1/cases/{case_id}").json()["status"] == "APPROVED"
        assert client.get(f"/v1/cases/{case_id}/view").json()["soar"] is None

    working = create_app(db_path=db_path, soar_client=FakeSoarClient())
    with TestClient(working) as client:
        submission = submit(client, case_id)
        assert submission["soar_case_id"] == f"SOAR-{case_id}-0001"
        assert client.get(f"/v1/cases/{case_id}").json()["status"] == "SUBMITTED"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/cases/{case_id}",
        "/v1/cases/{case_id}/evidence",
        "/v1/cases/{case_id}/history",
        "/v1/cases/{case_id}/view",
    ],
)
def test_get_routes_non_int_id_returns_422(client: TestClient, path: str) -> None:
    response = client.get(path.format(case_id="abc"))
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert set(body) == {"error", "message", "details"}
