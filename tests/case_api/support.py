"""Shared request helpers for the case API tests."""

from typing import Any, cast

from fastapi.testclient import TestClient
from httpx import Response

ANALYST = {"id": "analyst-1", "type": "analyst"}
AGENT = {"id": "evidence-sentinel", "type": "agent"}
SYSTEM = {"id": "splunk-watcher", "type": "system"}
DISPOSITION_ACTOR = {"id": "disposition-sentinel", "type": "agent"}


def expect_json(response: Response, status_code: int) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body: object = response.json()
    assert isinstance(body, dict)
    return cast(dict[str, Any], body)


def ingest(
    client: TestClient,
    alert_id: str = "ALT-1",
    *,
    actor: dict[str, str] | None = None,
    splunk_severity: str = "high",
    title: str = "Suspicious login",
) -> dict[str, Any]:
    return expect_json(
        client.post(
            "/v1/cases",
            json={
                "alert_id": alert_id,
                "title": title,
                "splunk_severity": splunk_severity,
                "summary": "repeated failures from one host",
                "assigned_analyst": "sam",
                "actor": actor or SYSTEM,
            },
        ),
        201,
    )


def triage(
    client: TestClient,
    case_id: int,
    *,
    actor: dict[str, str] | None = None,
    severity: str = "critical",
) -> dict[str, Any]:
    return expect_json(
        client.post(
            f"/v1/cases/{case_id}/triage",
            json={
                "severity": severity,
                "rationale": "matches a known intrusion pattern",
                "model": "grok-3",
                "actor": actor or {"id": "triage-agent", "type": "agent"},
            },
        ),
        200,
    )


def add_evidence(
    client: TestClient,
    case_id: int,
    *,
    actor: dict[str, str] | None = None,
) -> dict[str, Any]:
    return expect_json(
        client.post(
            f"/v1/cases/{case_id}/evidence",
            json={
                "source": "mock_splunk",
                "ref": "idx=auth earliest=-15m",
                "content": {"src": "10.0.0.8", "failures": 42},
                "actor": actor or AGENT,
            },
        ),
        201,
    )


def add_finding(
    client: TestClient,
    case_id: int,
    evidence_ids: list[int],
    *,
    actor: dict[str, str] | None = None,
    kind: str = "observation",
    statement: str = "forty failures from one host",
    confidence: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "statement": statement,
        "evidence_ids": evidence_ids,
        "actor": actor or {"id": "finding-agent", "type": "agent"},
    }
    if confidence is not None:
        payload["confidence"] = confidence
    return expect_json(client.post(f"/v1/cases/{case_id}/findings", json=payload), 201)


def propose(
    client: TestClient,
    case_id: int,
    finding_ids: list[int],
    *,
    actor: dict[str, str] | None = None,
) -> dict[str, Any]:
    return expect_json(
        client.post(
            f"/v1/cases/{case_id}/disposition",
            json={
                "action": "contain",
                "rationale": "block the source",
                "finding_ids": finding_ids,
                "actor": actor or DISPOSITION_ACTOR,
            },
        ),
        201,
    )


def decide(
    client: TestClient,
    case_id: int,
    *,
    decision: str = "approve",
    actor: dict[str, str] | None = None,
    rationale: str = "containment is warranted",
) -> dict[str, Any]:
    return expect_json(
        client.post(
            f"/v1/cases/{case_id}/decision",
            json={
                "decision": decision,
                "rationale": rationale,
                "actor": actor or ANALYST,
            },
        ),
        201,
    )


def submit(
    client: TestClient,
    case_id: int,
    *,
    actor: dict[str, str] | None = None,
) -> dict[str, Any]:
    return expect_json(
        client.post(
            f"/v1/cases/{case_id}/soar-submission",
            json={"actor": actor or {"id": "soar-bot", "type": "system"}},
        ),
        201,
    )


def all_events(client: TestClient, case_id: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"after_id": 0, "limit": 100}
    if case_id is not None:
        params["case_id"] = case_id
    response = client.get("/v1/events", params=params)
    body = expect_json(response, 200)
    items: object = body["items"]
    assert isinstance(items, list)
    return cast(list[dict[str, Any]], items)


def walk_to_proposed(client: TestClient, alert_id: str) -> dict[str, Any]:
    case = ingest(client, alert_id)
    case_id = case["id"]
    triage(client, case_id)
    evidence = add_evidence(client, case_id)
    finding = add_finding(client, case_id, [evidence["id"]])
    disposition = propose(client, case_id, [finding["id"]])
    return {
        "case_id": case_id,
        "evidence": evidence,
        "finding": finding,
        "disposition": disposition,
    }


def walk_to_submitted(client: TestClient, alert_id: str) -> dict[str, Any]:
    proposed = walk_to_proposed(client, alert_id)
    decide(client, proposed["case_id"])
    proposed["submission"] = submit(client, proposed["case_id"])
    return proposed
