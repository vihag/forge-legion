"""Case API metrics endpoint (C7 M1–M4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.case_api.app import create_app
from services.case_api.store import Store

ACTOR = {"actor_id": "seed", "actor_type": "system", "component": "test"}


def _insert_case(conn: sqlite3.Connection, **row: Any) -> None:
    conn.execute(
        """
        INSERT INTO cases (
            id, alert_id, title, summary, splunk_severity, triage_severity,
            triage_rationale, triage_model, assigned_analyst, status,
            created_at, updated_at, alert_time, route, verdict,
            prompt_tokens, completion_tokens
        ) VALUES (
            :id, :alert_id, :title, NULL, :splunk_severity, :triage_severity,
            NULL, NULL, NULL, :status,
            :created_at, :updated_at, :alert_time, :route, :verdict,
            :prompt_tokens, :completion_tokens
        )
        """,
        row,
    )


def _insert_audit(
    conn: sqlite3.Connection,
    *,
    case_id: int,
    ts: str,
    event_type: str,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_events (
            case_id, ts, type, actor_id, actor_type, component,
            from_status, to_status, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, '{}')
        """,
        (case_id, ts, event_type, ACTOR["actor_id"], ACTOR["actor_type"], ACTOR["component"]),
    )


@pytest.fixture
def metrics_client(tmp_path: Path) -> TestClient:
    db_path = str(tmp_path / "metrics.db")
    app = create_app(db_path=db_path)
    store: Store = app.state.store
    base = "2026-01-01T00:00:00Z"
    with store.transaction() as conn:
        _insert_case(
            conn,
            id=1,
            alert_id="M-NEW",
            title="new alert",
            splunk_severity="low",
            triage_severity=None,
            status="NEW",
            created_at=base,
            updated_at=base,
            alert_time=base,
            route=None,
            verdict=None,
            prompt_tokens=None,
            completion_tokens=None,
        )
        _insert_case(
            conn,
            id=2,
            alert_id="M-COLLAB",
            title="collaborative triaged",
            splunk_severity="low",
            triage_severity="high",
            status="TRIAGED",
            created_at=base,
            updated_at="2026-01-01T00:05:00Z",
            alert_time=base,
            route="collaborative",
            verdict="suspicious",
            prompt_tokens=None,
            completion_tokens=None,
        )
        _insert_audit(conn, case_id=2, ts="2026-01-01T00:01:40Z", event_type="case.triaged")
        _insert_case(
            conn,
            id=3,
            alert_id="M-AUTO",
            title="auto resolved",
            splunk_severity="medium",
            triage_severity="low",
            status="AUTO_RESOLVED",
            created_at=base,
            updated_at="2026-01-01T00:10:00Z",
            alert_time=base,
            route="autonomous",
            verdict="benign",
            prompt_tokens=1_000_000,
            completion_tokens=None,
        )
        _insert_audit(conn, case_id=3, ts="2026-01-01T00:02:00Z", event_type="case.triaged")
        _insert_audit(conn, case_id=3, ts="2026-01-01T00:02:40Z", event_type="case.auto_resolved")
        _insert_case(
            conn,
            id=4,
            alert_id="M-REVIEW",
            title="needs review",
            splunk_severity="high",
            triage_severity="high",
            status="NEEDS_REVIEW",
            created_at=base,
            updated_at="2026-01-01T00:15:00Z",
            alert_time=base,
            route="review",
            verdict=None,
            prompt_tokens=None,
            completion_tokens=None,
        )
        _insert_audit(conn, case_id=4, ts="2026-01-01T00:05:00Z", event_type="case.triaged")
        _insert_case(
            conn,
            id=5,
            alert_id="M-APPROVED",
            title="approved disposition",
            splunk_severity="medium",
            triage_severity="medium",
            status="APPROVED",
            created_at=base,
            updated_at="2026-01-01T00:20:00Z",
            alert_time=base,
            route="collaborative",
            verdict="false_positive",
            prompt_tokens=None,
            completion_tokens=500_000,
        )
        _insert_audit(conn, case_id=5, ts="2026-01-01T00:03:20Z", event_type="case.triaged")
        _insert_audit(conn, case_id=5, ts="2026-01-01T00:04:30Z", event_type="disposition.approved")
    client = TestClient(app)
    yield client
    store.close()


def test_m1_fixture_store_exact_metrics(metrics_client: TestClient) -> None:
    expected = {
        "counts": {
            "total": 5,
            "by_status": {
                "NEW": 1,
                "TRIAGED": 1,
                "INVESTIGATING": 0,
                "DISPOSITION_PROPOSED": 0,
                "APPROVED": 1,
                "REJECTED": 0,
                "SUBMITTED": 0,
                "NEEDS_REVIEW": 1,
                "AUTO_RESOLVED": 1,
            },
            "by_route": {"autonomous": 1, "collaborative": 2, "review": 1},
            "auto_resolved": 1,
            "collaborative_open": 1,
            "needs_review": 1,
        },
        "uplifts": {"low->high": 1},
        "downgrades": 1,
        "time_to_triage_s": {"n": 4, "median": 160.0, "average": 180.0},
        "time_to_resolve_s": {
            "ai_only": {"n": 1, "median": 160.0, "average": 160.0},
            "ai_plus_human": {"n": 1, "median": 270.0, "average": 270.0},
        },
        "tokens": {
            "prompt": 1_000_000,
            "completion": 500_000,
            "total": 1_500_000,
            "per_case_average": 750000.0,
        },
        "cost_usd": None,
        "auto_resolve_enabled": False,
        "minutes_saved_estimate": 15,
        "assumptions": {"minutes_per_alert": 15},
    }
    body = metrics_client.get("/v1/metrics").json()
    for key, value in expected.items():
        assert body[key] == value, (key, body[key], value)
    assert body["time_to_triage_s"] == expected["time_to_triage_s"]
    empty = {"n": 0, "median": None, "average": None}
    assert metrics_client.get("/v1/metrics").json()["agents"] == []


def test_m1b_auto_resolve_enabled_flag(
    metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert metrics_client.get("/v1/metrics").json()["auto_resolve_enabled"] is False
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    assert metrics_client.get("/v1/metrics").json()["auto_resolve_enabled"] is True


def test_m2_cost_usd_when_prices_set(
    metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert metrics_client.get("/v1/metrics").json()["cost_usd"] is None
    monkeypatch.setenv("GROK_PRICE_INPUT_PER_MTOK", "2")
    monkeypatch.setenv("GROK_PRICE_OUTPUT_PER_MTOK", "10")
    assert metrics_client.get("/v1/metrics").json()["cost_usd"] == 7.0


def test_m3_since_excludes_earlier_cases(metrics_client: TestClient) -> None:
    body = metrics_client.get("/v1/metrics", params={"since": "2026-01-02T00:00:00Z"}).json()
    assert body["counts"]["total"] == 0
    assert body["time_to_triage_s"] == {"n": 0, "median": None, "average": None}


def test_m4_metrics_read_only(metrics_client: TestClient) -> None:
    store: Store = metrics_client.app.state.store
    with store.transaction() as conn:
        before = store.count_audit_events(conn)
    metrics_client.get("/v1/metrics")
    with store.transaction() as conn:
        after = store.count_audit_events(conn)
    assert before == after


def test_empty_timing_nulls(metrics_client: TestClient) -> None:
    body = metrics_client.get("/v1/metrics", params={"since": "2026-01-02T00:00:00Z"}).json()
    null_block = {"n": 0, "median": None, "average": None}
    assert body["time_to_triage_s"] == null_block
    assert body["time_to_resolve_s"]["ai_only"] == null_block
    assert body["time_to_resolve_s"]["ai_plus_human"] == null_block


def test_recent_cases_order(metrics_client: TestClient) -> None:
    recent = metrics_client.get("/v1/metrics").json()["recent"]
    assert [item["alert_id"] for item in recent] == [
        "M-APPROVED",
        "M-REVIEW",
        "M-AUTO",
        "M-COLLAB",
        "M-NEW",
    ]
    sample = recent[0]
    assert set(sample.keys()) == {
        "id",
        "alert_id",
        "title",
        "status",
        "route",
        "splunk_severity",
        "triage_severity",
        "verdict",
        "updated_at",
    }