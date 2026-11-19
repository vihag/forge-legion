"""DecisionCreate.source rules (C6 §6.1 A8, A10, A11)."""

from fastapi.testclient import TestClient

from tests.case_api.support import (
    AGENT,
    ANALYST,
    add_evidence,
    add_finding,
    decide,
    ingest,
    propose,
    triage,
)


def _proposed(client: TestClient) -> int:
    case = ingest(client, "ALT-SOURCE")
    triage(client, case["id"])
    evidence = add_evidence(client, case["id"])
    finding = add_finding(client, case["id"], [evidence["id"]])
    propose(client, case["id"], [finding["id"]])
    return case["id"]


def test_old_style_decision_without_source_unchanged(client: TestClient) -> None:
    case_id = _proposed(client)
    decide(client, case_id)
    view = client.get(f"/v1/cases/{case_id}/view").json()
    approved = [e for e in view["audit"] if e["type"] == "disposition.approved"]
    assert len(approved) == 1
    assert "source" not in approved[0]["payload"]


def test_instruction_mismatch_rejects_with_no_audit(client: TestClient) -> None:
    case_id = _proposed(client)
    before = client.get(f"/v1/cases/{case_id}/view").json()["audit"]
    response = client.post(
        f"/v1/cases/{case_id}/decision",
        json={
            "decision": "reject",
            "rationale": "no",
            "actor": ANALYST,
            "source": {
                "channel": "grok_bot_chat",
                "chat_ref": "chat-1",
                "instruction_text": "approve it",
                "relayed_by": "mcp:centurion",
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "instruction_mismatch"
    after = client.get(f"/v1/cases/{case_id}/view").json()["audit"]
    assert len(after) == len(before)


def test_agent_with_source_still_forbidden(client: TestClient) -> None:
    case_id = _proposed(client)
    response = client.post(
        f"/v1/cases/{case_id}/decision",
        json={
            "decision": "approve",
            "rationale": "no",
            "actor": AGENT,
            "source": {
                "channel": "grok_bot_chat",
                "chat_ref": "c",
                "instruction_text": "approve",
                "relayed_by": "mcp:bot",
            },
        },
    )
    assert response.status_code == 403


def test_case_view_includes_alert_and_timestamps(client: TestClient) -> None:
    case = ingest(client, "ALT-VIEW-FIELDS")
    view = client.get(f"/v1/cases/{case['id']}/view").json()
    assert view["alert_id"] == "ALT-VIEW-FIELDS"
    assert view["created_at"]
    assert view["updated_at"]


def test_happy_source_in_audit_payload(client: TestClient) -> None:
    case_id = _proposed(client)
    client.post(
        f"/v1/cases/{case_id}/decision",
        json={
            "decision": "approve",
            "rationale": "confirmed",
            "actor": {"id": "vihag", "type": "analyst"},
            "source": {
                "channel": "grok_bot_chat",
                "chat_ref": "centurion-chat",
                "instruction_text": "Approve case 1",
                "relayed_by": "mcp:centurion",
            },
        },
    )
    view = client.get(f"/v1/cases/{case_id}/view").json()
    approved = [e for e in view["audit"] if e["type"] == "disposition.approved"]
    assert len(approved) == 1
    assert approved[0]["actor"] == {"id": "vihag", "type": "analyst"}
    src = approved[0]["payload"]["source"]
    assert src["instruction_text"] == "Approve case 1"
    assert src["relayed_by"] == "mcp:centurion"