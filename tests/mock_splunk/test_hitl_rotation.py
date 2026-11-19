"""C8 HITL rotation QA (HL1, HL2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.mock_splunk.app import create_app
from services.mock_splunk.stream import generate, hitl_total, reset_registry


def _hitl_ids(items) -> list[str]:
    return [item.scenario_id for item in items if item.pool == "hitl"]


def test_hl1_no_repeat_within_full_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "tests/fixtures/scenarios/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(root / "tests/fixtures/scenarios"))
    from services.mock_splunk.stream import reset_registry

    reset_registry()
    for seed in (42, 7):
        used: frozenset[str] = frozenset()
        rows: list[str] = []
        for _ in range(300):
            batch, used = generate(seed, 1, start_index=len(rows), hitl_used=used)
            rows.extend(_hitl_ids(batch))
        from services.mock_splunk.stream import list_scenarios

        total = hitl_total(list_scenarios())
        for start in range(0, len(rows) - total + 1, total):
            window = rows[start : start + total]
            if len(window) < total:
                break
            assert len(window) == len(set(window))


def test_hl2_reset_clears_hitl_used_stop_start_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "tests/fixtures/scenarios/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(root / "tests/fixtures/scenarios"))
    reset_registry()
    app = create_app()
    client = TestClient(app)
    store = app.state.store
    client.post("/v1/reset", json={})
    store._hitl_used = {"spl-hitl-confirm"}
    client.post("/v1/stream/start", json={"mode": "random"})
    used_before = client.get("/v1/stream/status").json()["hitl_used"]
    assert used_before > 0
    client.post("/v1/stream/stop", json={})
    stopped_status = client.get("/v1/stream/status").json()["hitl_used"]
    assert stopped_status == used_before
    client.post("/v1/reset", json={})
    assert client.get("/v1/stream/status").json()["hitl_used"] == 0