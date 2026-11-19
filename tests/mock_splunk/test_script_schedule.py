"""Script mode plays config/demo_script.yaml in at_s order."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.mock_splunk import stream

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config_scenarios(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCENARIO_WORLD", str(ROOT / "config/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(ROOT / "config/scenarios"))
    monkeypatch.delenv("SCENARIO_SCRIPT", raising=False)
    stream.reset_registry()
    yield
    stream.reset_registry()


def test_script_schedule_follows_demo_script(config_scenarios: None) -> None:
    raw = yaml.safe_load((ROOT / "config/demo_script.yaml").read_text(encoding="utf-8"))
    expected = [e["scenario"] for e in sorted(raw["events"], key=lambda e: int(e["at_s"]))]
    schedule = stream.script_schedule(stream.list_scenarios())
    assert [s.id for s in schedule] == expected
    assert len(schedule) == 36


def test_script_has_gate_refused_medium_case_near_three_minutes(config_scenarios: None) -> None:
    schedule = stream.script_schedule(stream.list_scenarios())
    # One event per 20 s tick at 3/min: index k fires at about 20 * (k + 1) s.
    window = [s for k, s in enumerate(schedule) if 150 <= 20 * (k + 1) <= 180]
    assert any(s.id == "hitl-confirm-findb-index-maintenance" for s in window)


def test_script_schedule_falls_back_to_demo_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCENARIO_WORLD", str(ROOT / "tests/fixtures/scenarios/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(ROOT / "tests/fixtures/scenarios"))
    monkeypatch.setenv("SCENARIO_SCRIPT", str(ROOT / "does-not-exist.yaml"))
    stream.reset_registry()
    try:
        schedule = stream.script_schedule(stream.list_scenarios())
        assert all(s.demo_slot is not None for s in schedule)
    finally:
        stream.reset_registry()
