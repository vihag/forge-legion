"""C8 with_legion_open metrics steer."""

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


@pytest.fixture
def metrics_db(tmp_path: Path) -> TestClient:
    db_path = str(tmp_path / "legion.db")
    app = create_app(db_path=db_path)
    store: Store = app.state.store
    base = "2026-01-01T00:00:00Z"
    with store.transaction() as conn:
        _insert_case(
            conn,
            id=1,
            alert_id="GATE-FAIL",
            title="gate refused",
            splunk_severity="low",
            triage_severity="low",
            status="TRIAGED",
            created_at=base,
            updated_at=base,
            alert_time=base,
            route="autonomous",
            verdict="benign",
            prompt_tokens=None,
            completion_tokens=None,
        )
        _insert_case(
            conn,
            id=2,
            alert_id="AUTO-OK",
            title="auto resolved",
            splunk_severity="low",
            triage_severity="low",
            status="AUTO_RESOLVED",
            created_at=base,
            updated_at=base,
            alert_time=base,
            route="autonomous",
            verdict="benign",
            prompt_tokens=None,
            completion_tokens=None,
        )
        conn.execute(
            """
            INSERT INTO audit_events (
                case_id, ts, type, actor_id, actor_type, component,
                from_status, to_status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, '{}')
            """,
            (1, base, "case.triaged", ACTOR["actor_id"], ACTOR["actor_type"], ACTOR["component"]),
        )
    client = TestClient(app)
    yield client
    store.close()


def test_with_legion_open_counts_gate_refused_autonomous_not_auto_resolved(
    metrics_db: TestClient,
) -> None:
    counts = metrics_db.get("/v1/metrics").json()["counts"]
    assert counts["with_legion_open"] == 1