"""Autonomous auto-resolve policy gate (C7 P1–P8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.case_api.app import create_app
from services.case_api.soar import FakeSoarClient

from .support import (
    ANALYST,
    SYSTEM,
    add_evidence,
    add_finding,
    all_events,
    decide,
    expect_json,
    ingest,
    triage,
    walk_to_proposed,
)

POLICY_ACTOR = {"id": "policy:autonomous-triage", "type": "system"}
AGENT = {"id": "auto-agent", "type": "agent"}


def _auto_resolve(
    client: TestClient,
    case_id: int,
    triage_evidence_id: int,
    *,
    actor: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"/v1/cases/{case_id}/auto-resolve",
        json={
            "policy_id": "autonomous-triage",
            "triage_evidence_id": triage_evidence_id,
            "actor": actor or POLICY_ACTOR,
        },
    )


def _apply_triage_fields(
    client: TestClient,
    case_id: int,
    *,
    triage_severity: str = "low",
    verdict: str = "benign",
    confidence: float = 0.9,
    route: str = "autonomous",
    privileged_account: bool = False,
    crown_jewel_asset: bool = False,
    recommended_action: str = "none",
) -> int:
    triage_ev = expect_json(
        client.post(
            f"/v1/cases/{case_id}/evidence",
            json={
                "source": "triage_assessment",
                "ref": f"tri-{case_id}",
                "content": {},
                "actor": SYSTEM,
            },
        ),
        201,
    )
    expect_json(
        client.post(
            f"/v1/cases/{case_id}/triage",
            json={
                "severity": triage_severity,
                "rationale": "policy",
                "model": "fixture",
                "actor": SYSTEM,
                "route": route,
                "rule": "test_rule",
                "verdict": verdict,
                "recommended_action": recommended_action,
                "confidence": confidence,
                "privileged_account": privileged_account,
                "crown_jewel_asset": crown_jewel_asset,
            },
        ),
        200,
    )
    return triage_ev["id"]


def test_p1_benign_autonomous_auto_resolves(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    case = ingest(client, "ALT-P1", splunk_severity="low")
    triage_ev_id = _apply_triage_fields(client, case["id"])
    events = all_events(client, case["id"])
    before = len([event for event in events if event["type"] == "case.auto_resolved"])
    resolved = expect_json(_auto_resolve(client, case["id"], triage_ev_id), 200)
    assert resolved["status"] == "AUTO_RESOLVED"
    after = len(
        [event for event in all_events(client, case["id"]) if event["type"] == "case.auto_resolved"]
    )
    assert after == before + 1


@pytest.mark.parametrize(
    ("env", "kwargs", "gate"),
    [
        ({"AUTO_RESOLVE_ENABLED": "0"}, {}, "G1"),
        ({}, {"route": "collaborative"}, "G2"),
        ({}, {"verdict": "suspicious"}, "G3"),
        ({}, {"recommended_action": "escalate"}, "G4"),
        ({}, {"triage_severity": "high"}, "G5"),
        ({}, {"privileged_account": True}, "G7"),
        ({}, {"crown_jewel_asset": True}, "G8"),
        ({"AUTO_RESOLVE_MIN_CONFIDENCE": "0.95"}, {"confidence": 0.9}, "G9"),
    ],
)
def test_p2_gate_failures(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    kwargs: dict[str, Any],
    gate: str,
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", env.get("AUTO_RESOLVE_ENABLED", "1"))
    case = ingest(client, f"ALT-{gate}", splunk_severity="low")
    triage_ev_id = _apply_triage_fields(client, case["id"], **kwargs)
    body = expect_json(_auto_resolve(client, case["id"], triage_ev_id), 409)
    assert body["error"] == "policy_gate_failed"
    assert gate in body["details"]["failed"]
    assert client.get(f"/v1/cases/{case['id']}").json()["status"] == "TRIAGED"
    resolved_events = [
        event for event in all_events(client, case["id"]) if event["type"] == "case.auto_resolved"
    ]
    assert not resolved_events


def test_p2_g6_splunk_severity_high(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    case = ingest(client, "ALT-G6", splunk_severity="high")
    triage_ev_id = _apply_triage_fields(client, case["id"])
    body = expect_json(_auto_resolve(client, case["id"], triage_ev_id), 409)
    assert "G6" in body["details"]["failed"]


def test_p2_g10_wrong_evidence_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    case = ingest(client, "ALT-G10", splunk_severity="low")
    _apply_triage_fields(client, case["id"])
    other = add_evidence(client, case["id"])
    body = expect_json(_auto_resolve(client, case["id"], other["id"]), 409)
    assert "G10" in body["details"]["failed"]


@pytest.mark.parametrize("actor", [ANALYST, AGENT])
def test_p3_non_policy_actor_forbidden(client: TestClient, actor: dict[str, str]) -> None:
    case = ingest(client, "ALT-P3")
    triage_ev_id = _apply_triage_fields(client, case["id"])
    expect_json(_auto_resolve(client, case["id"], triage_ev_id, actor=actor), 403)


def test_p4_auto_resolve_from_new_and_investigating(client: TestClient) -> None:
    fresh = ingest(client, "ALT-P4A")
    triage_ev = add_evidence(client, fresh["id"])
    expect_json(_auto_resolve(client, fresh["id"], triage_ev["id"]), 409)

    case = ingest(client, "ALT-P4B")
    case_id = triage(client, case["id"])["id"]
    ev = add_evidence(client, case_id)
    add_finding(client, case_id, [ev["id"]])
    assert client.get(f"/v1/cases/{case_id}").json()["status"] == "INVESTIGATING"
    assessment = expect_json(
        client.post(
            f"/v1/cases/{case_id}/evidence",
            json={
                "source": "triage_assessment",
                "ref": "p4",
                "content": {},
                "actor": SYSTEM,
            },
        ),
        201,
    )
    expect_json(_auto_resolve(client, case_id, assessment["id"]), 409)


def test_p5_operations_on_auto_resolved_are_denied(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    case = ingest(client, "ALT-P5", splunk_severity="low")
    triage_ev_id = _apply_triage_fields(client, case["id"])
    case_id = expect_json(_auto_resolve(client, case["id"], triage_ev_id), 200)["id"]
    expect_json(
        client.post(
            f"/v1/cases/{case_id}/triage",
            json={"severity": "low", "rationale": "x", "model": "m", "actor": SYSTEM},
        ),
        409,
    )
    expect_json(
        client.post(
            f"/v1/cases/{case_id}/evidence",
            json={"source": "x", "ref": "r", "content": {}, "actor": AGENT},
        ),
        409,
    )
    expect_json(
        client.post(
            f"/v1/cases/{case_id}/review-flag",
            json={"reason": "model_call_failed", "detail": "x", "actor": SYSTEM},
        ),
        409,
    )
    expect_json(_auto_resolve(client, case_id, triage_ev_id), 409)


def test_p6_submit_to_soar_on_auto_resolved_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracking = FakeSoarClient()
    submits: list[int] = []

    def counted_submit(request: object) -> str:
        submits.append(1)
        return "SOAR-1-0001"

    tracking.submit = counted_submit  # type: ignore[method-assign]
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    app = create_app(db_path=str(tmp_path / "cases.db"), soar_client=tracking)
    with TestClient(app) as client:
        case = ingest(client, "ALT-P6", splunk_severity="low")
        triage_ev_id = _apply_triage_fields(client, case["id"])
        case_id = expect_json(_auto_resolve(client, case["id"], triage_ev_id), 200)["id"]
        expect_json(
            client.post(f"/v1/cases/{case_id}/soar-submission", json={"actor": SYSTEM}),
            409,
        )
        assert submits == []


def test_p7_submit_to_soar_requires_analyst_approval(tmp_path: Path) -> None:
    app = create_app(db_path=str(tmp_path / "cases.db"))
    with TestClient(app) as client:
        proposed = walk_to_proposed(client, "ALT-P7")
        case_id = proposed["case_id"]
        decide(client, case_id)
        conn = sqlite3.connect(str(tmp_path / "cases.db"))
        try:
            conn.execute("DELETE FROM decisions WHERE case_id = ?", (case_id,))
            conn.commit()
        finally:
            conn.close()
        response = client.post(
            f"/v1/cases/{case_id}/soar-submission",
            json={"actor": SYSTEM},
        )
        body = expect_json(response, 409)
        assert body["error"] == "no_analyst_approval"