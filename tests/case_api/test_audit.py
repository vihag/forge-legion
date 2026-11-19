"""Every successful mutation writes one audit event. Reads and rejections write none."""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .support import (
    ANALYST,
    add_evidence,
    add_finding,
    all_events,
    decide,
    ingest,
    propose,
    submit,
    triage,
    walk_to_proposed,
    walk_to_submitted,
)

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
HAPPY_TYPES = [
    "case.ingested",
    "case.triaged",
    "evidence.added",
    "finding.recorded",
    "disposition.proposed",
    "disposition.approved",
    "soar.submitted",
]


def _perform(
    client: TestClient,
    step: str,
    alert_id: str,
    actor: dict[str, str],
) -> tuple[int, int]:
    if step == "ingest":
        before = len(all_events(client))
        case = ingest(client, alert_id, actor=actor)
        return before, int(case["id"])
    case = ingest(client, alert_id)
    case_id = int(case["id"])
    if step == "triage":
        before = len(all_events(client))
        triage(client, case_id, actor=actor)
        return before, case_id
    if step == "evidence":
        before = len(all_events(client))
        add_evidence(client, case_id, actor=actor)
        return before, case_id
    triage(client, case_id)
    evidence = add_evidence(client, case_id)
    if step == "finding":
        before = len(all_events(client))
        add_finding(client, case_id, [evidence["id"]], actor=actor)
        return before, case_id
    finding = add_finding(client, case_id, [evidence["id"]])
    if step == "disposition":
        before = len(all_events(client))
        propose(client, case_id, [finding["id"]], actor=actor)
        return before, case_id
    propose(client, case_id, [finding["id"]])
    if step == "decision":
        before = len(all_events(client))
        decide(client, case_id, actor=actor)
        return before, case_id
    decide(client, case_id)
    if step == "soar":
        before = len(all_events(client))
        submit(client, case_id, actor=actor)
        return before, case_id
    raise AssertionError(f"unknown step {step}")


@pytest.mark.parametrize(
    ("step", "event_type", "from_status", "to_status", "actor"),
    [
        ("ingest", "case.ingested", None, "NEW", {"id": "ingest-bot", "type": "system"}),
        ("triage", "case.triaged", "NEW", "TRIAGED", {"id": "triage-agent", "type": "agent"}),
        (
            "evidence",
            "evidence.added",
            "NEW",
            "NEW",
            {"id": "evidence-sentinel", "type": "agent"},
        ),
        (
            "finding",
            "finding.recorded",
            "TRIAGED",
            "INVESTIGATING",
            {"id": "finding-agent", "type": "agent"},
        ),
        (
            "disposition",
            "disposition.proposed",
            "INVESTIGATING",
            "DISPOSITION_PROPOSED",
            {"id": "disposition-sentinel", "type": "agent"},
        ),
        (
            "decision",
            "disposition.approved",
            "DISPOSITION_PROPOSED",
            "APPROVED",
            {"id": "analyst-1", "type": "analyst"},
        ),
        ("soar", "soar.submitted", "APPROVED", "SUBMITTED", {"id": "soar-bot", "type": "system"}),
    ],
)
def test_each_mutating_endpoint_writes_exactly_one_event(
    client: TestClient,
    step: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    actor: dict[str, str],
) -> None:
    before, case_id = _perform(client, step, f"ALT-{step}", actor)
    events = all_events(client)
    assert len(events) == before + 1
    event = events[-1]
    assert event["type"] == event_type
    assert event["actor"] == actor
    assert event["from_status"] == from_status
    assert event["to_status"] == to_status
    assert TS.match(event["ts"])
    assert event["component"] == "case_api"
    assert event["case_id"] == case_id
    if event_type == "evidence.added":
        assert "content" not in event["payload"]
        assert event["payload"]["source"] == "mock_splunk"
        assert "failures" not in event["payload"]


def test_rejected_call_writes_no_event(client: TestClient) -> None:
    case = ingest(client, "ALT-REJECTED-CALL")
    case_id = case["id"]
    before = all_events(client)
    response = client.post(
        f"/v1/cases/{case_id}/findings",
        json={
            "kind": "hypothesis",
            "statement": "too early",
            "evidence_ids": [],
            "actor": {"id": "finding-agent", "type": "agent"},
        },
    )
    assert response.status_code == 409
    assert all_events(client) == before
    duplicate = client.post(
        "/v1/cases",
        json={
            "alert_id": "ALT-REJECTED-CALL",
            "title": "again",
            "splunk_severity": "low",
            "actor": {"id": "splunk-watcher", "type": "system"},
        },
    )
    assert duplicate.status_code == 409
    assert all_events(client) == before


def test_get_endpoints_write_no_event(client: TestClient) -> None:
    case = ingest(client, "ALT-READS")
    case_id = case["id"]
    triage(client, case_id)
    add_evidence(client, case_id)
    before = all_events(client)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/v1/cases").status_code == 200
    assert client.get(f"/v1/cases/{case_id}").status_code == 200
    assert client.get(f"/v1/cases/{case_id}/evidence").status_code == 200
    assert client.get(f"/v1/cases/{case_id}/history").status_code == 200
    assert client.get(f"/v1/cases/{case_id}/view").status_code == 200
    assert client.get("/v1/events").status_code == 200
    assert all_events(client) == before


def test_happy_path_history_is_ordered(client: TestClient) -> None:
    case = ingest(client, "ALT-HISTORY")
    case_id = case["id"]
    triage(client, case_id)
    evidence = add_evidence(client, case_id)
    finding = add_finding(client, case_id, [evidence["id"]])
    propose(client, case_id, [finding["id"]])
    decide(client, case_id)
    submit(client, case_id)
    response = client.get(f"/v1/cases/{case_id}/history")
    assert response.status_code == 200
    history: list[dict[str, Any]] = response.json()
    assert [event["type"] for event in history] == HAPPY_TYPES
    ids = [event["id"] for event in history]
    assert ids == sorted(ids)
    assert ids == list(range(ids[0], ids[0] + len(ids)))
    for event in history:
        assert event["case_id"] == case_id
        assert event["component"] == "case_api"
        assert TS.match(event["ts"])


def test_events_stream_paginates_with_after_id(client: TestClient) -> None:
    walked = walk_to_submitted(client, "ALT-PAGE")
    extra = ingest(client, "ALT-PAGE-2")
    seen: list[int] = []
    cursor = 0
    for _ in range(10):
        response = client.get("/v1/events", params={"after_id": cursor, "limit": 3})
        assert response.status_code == 200
        body = response.json()
        ids = [item["id"] for item in body["items"]]
        if not ids:
            assert body["next_after_id"] == cursor
            break
        assert ids == sorted(ids)
        assert ids[0] > cursor
        assert body["next_after_id"] == ids[-1]
        seen.extend(ids)
        cursor = body["next_after_id"]
    else:
        raise AssertionError("pagination did not terminate")
    full = client.get("/v1/events", params={"after_id": 0, "limit": 100}).json()["items"]
    assert [item["id"] for item in full] == seen
    assert len(seen) == len(set(seen))
    filtered = client.get(
        "/v1/events",
        params={"after_id": 0, "case_id": walked["case_id"], "limit": 100},
    )
    assert filtered.status_code == 200
    filtered_items = filtered.json()["items"]
    assert [item["type"] for item in filtered_items] == HAPPY_TYPES
    assert all(item["case_id"] == walked["case_id"] for item in filtered_items)
    other = client.get("/v1/events", params={"after_id": 0, "case_id": extra["id"]})
    assert [item["type"] for item in other.json()["items"]] == ["case.ingested"]
    assert client.get("/v1/events", params={"after_id": -1}).status_code == 422


def test_reject_writes_exactly_one_disposition_rejected_event(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-REJECT-AUDIT")
    case_id = proposed["case_id"]
    before = all_events(client)
    decision = decide(client, case_id, decision="reject", rationale="need more evidence")
    events = all_events(client)
    assert len(events) == len(before) + 1
    event = events[-1]
    assert event["type"] == "disposition.rejected"
    assert event["from_status"] == "DISPOSITION_PROPOSED"
    assert event["to_status"] == "REJECTED"
    assert event["actor"] == ANALYST
    assert event["case_id"] == case_id
    assert event["payload"]["decision_id"] == decision["id"]
    assert event["payload"]["disposition_id"] == proposed["disposition"]["id"]
    assert [item["type"] for item in events].count("disposition.rejected") == 1


def test_decision_missing_rationale_422_writes_no_event(client: TestClient) -> None:
    proposed = walk_to_proposed(client, "ALT-NO-RATIONALE")
    case_id = proposed["case_id"]
    before = all_events(client)
    missing = client.post(
        f"/v1/cases/{case_id}/decision",
        json={"decision": "approve", "actor": ANALYST},
    )
    empty = client.post(
        f"/v1/cases/{case_id}/decision",
        json={"decision": "reject", "rationale": "", "actor": ANALYST},
    )
    assert missing.status_code == 422
    assert empty.status_code == 422
    assert missing.json()["error"] == "validation_error"
    assert empty.json()["error"] == "validation_error"
    assert all_events(client) == before
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "DISPOSITION_PROPOSED"


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        (
            None,
            {"alert_id": "ALT-NO-ACTOR", "title": "Missing actor", "splunk_severity": "low"},
        ),
        ("/triage", {"severity": "low", "rationale": "because", "model": "grok-3"}),
        ("/evidence", {"source": "mock_splunk", "ref": "EV-AUTH-0001", "content": {}}),
        (
            "/findings",
            {"kind": "hypothesis", "statement": "maybe", "evidence_ids": []},
        ),
        (
            "/disposition",
            {"action": "benign", "rationale": "noise", "finding_ids": [1]},
        ),
        ("/decision", {"decision": "approve", "rationale": "agree"}),
        ("/soar-submission", {}),
    ],
)
def test_mutating_call_missing_actor_422_writes_no_event(
    client: TestClient,
    suffix: str | None,
    payload: dict[str, Any],
) -> None:
    if suffix is None:
        before = all_events(client)
        response = client.post("/v1/cases", json=payload)
    else:
        case = ingest(client, "ALT-NO-ACTOR")
        before = all_events(client)
        response = client.post(f"/v1/cases/{case['id']}{suffix}", json=payload)
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert all_events(client) == before
