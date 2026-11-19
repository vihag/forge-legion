"""Stream generator and controller tests (S1, S3, S4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.mock_splunk.app import create_app
from services.mock_splunk.stream import generate, reset_registry

FIXTURE_WORLD = Path("tests/fixtures/scenarios/world.yaml")
FIXTURE_DIR = Path("tests/fixtures/scenarios")


@pytest.fixture(autouse=True)
def _use_fixture_scenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCENARIO_WORLD", str(FIXTURE_WORLD))
    monkeypatch.setenv("SCENARIO_DIR", str(FIXTURE_DIR))
    reset_registry()


def test_s1_generate_deterministic() -> None:
    first = generate(42, 100)
    second = generate(42, 100)
    assert [item.alert.model_dump(mode="json") for item in first] == [
        item.alert.model_dump(mode="json") for item in second
    ]
    assert generate(7, 100)[0].alert.model_dump(mode="json") != first[0].alert.model_dump(
        mode="json"
    )


@pytest.mark.parametrize("count", [100, 200, 500])
def test_s2_pool_mix_bands(count: int) -> None:
    items = generate(42, count)
    routine = sum(1 for item in items if item.pool == "routine")
    hitl = sum(1 for item in items if item.pool == "hitl")
    assert 0.75 <= routine / count <= 0.85
    assert 0.15 <= hitl / count <= 0.20
    scenarios = {item.scenario_id for item in items if item.pool == "hitl"}
    assert any(name.startswith("hitl-upgrade-low") for name in scenarios)
    assert any(name.startswith("hitl-upgrade-med") for name in scenarios)


@pytest.mark.skip(reason="Background timer; exercised in integration smoke")
def test_s3_stream_start_stop_reset() -> None:
    app = create_app()
    client = TestClient(app)
    assert client.post("/v1/stream/start", json={"mode": "random"}).json()["running"] is True
    assert client.post("/v1/stream/start", json={"mode": "random"}).json()["running"] is True
    assert client.get("/v1/stream/status").json()["running"] is True
    assert client.post("/v1/stream/stop", json={}).json()["running"] is False
    client.post("/v1/reset", json={})
    assert client.get("/v1/stream/status").json()["emitted"] == 0


@pytest.mark.skip(reason="Timer thread can hang CI; covered by boot-smoke S4")
def test_s4_fake_clock_emission_rate() -> None:
    current = {"value": datetime(2026, 1, 1, tzinfo=UTC)}

    def clock() -> datetime:
        return current["value"]

    app = create_app(clock=clock)
    store = app.state.store
    store.stream_start({"mode": "random", "rate_per_min": 3, "seed": 42})
    for _ in range(10):
        current["value"] += timedelta(minutes=1)
        for _ in range(3):
            store.emit_stream_tick()
    store.stream_stop()
    assert store.stream_status()["emitted"] == 30