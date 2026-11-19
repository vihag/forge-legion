"""C8 script planner QA (SB1, SB2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.mock_splunk.scenarios import check, load_scenario_file, scenario_from_file
from services.mock_splunk.store import SplunkStore
from services.mock_splunk.stream import plan_script


def test_sb1_plan_script_fixture_36_offsets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCENARIO_DIR", str(tmp_path))
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "tests/fixtures/scenarios/world.yaml"))
    from services.mock_splunk.stream import reset_registry

    reset_registry()
    yaml_path = tmp_path / "spl-demo-script.yaml"
    offsets = list(range(0, 721, 20))[:36]
    yaml_path.write_text(
        f"""
id: spl-demo-script
title: demo
splunk_rule_name: rule
splunk_severity: low
outcome_class: auto_benign
weight: 0
demo_offset_s: {offsets}
entities: {{}}
alert:
  title: t
  description: d
""".strip()
        + "\n",
        encoding="utf-8",
    )
    document = load_scenario_file(yaml_path)
    scenario = scenario_from_file(document)
    plan = plan_script((scenario,), seed=1)
    assert scenario.id == "spl-demo-script"
    assert len(plan) == 36
    assert plan == sorted(plan, key=lambda item: (item[0], item[1], item[2]))
    assert plan[-1][0] <= 720

    from datetime import UTC, datetime

    clock_times = [0]

    def clock() -> datetime:
        return datetime.fromtimestamp(clock_times[0], tz=UTC)

    store = SplunkStore(clock=clock)
    store._stream_mode = "script"
    store._stream_seed = 7
    store._script_plan = plan_script((scenario,), 7)
    store._stream_started_at = clock()
    for tick in range(900):
        clock_times[0] = tick
        store.emit_stream_tick()
    assert store._stream_emitted == 36


def test_sb2_demo_slot_plans_at_120_and_v8_both_fields(tmp_path: Path) -> None:
    world = Path(__file__).resolve().parents[2] / "tests/fixtures/scenarios/world.yaml"
    scenario_dir = tmp_path
    slot_file = scenario_dir / "spl-slot.yaml"
    slot_file.write_text(
        """
id: spl-slot
title: slot
splunk_rule_name: rule
splunk_severity: low
outcome_class: auto_benign
weight: 0
demo_slot: 2
entities: {}
alert:
  title: t
  description: d
""".strip()
        + "\n",
        encoding="utf-8",
    )
    scenario = scenario_from_file(load_scenario_file(slot_file))
    plan = plan_script((scenario,), 0)
    assert plan[0][0] == 120

    both = scenario_dir / "spl-both.yaml"
    both.write_text(
        (slot_file.read_text(encoding="utf-8") + "\ndemo_offset_s: 10\n"),
        encoding="utf-8",
    )
    errors = check(world, scenario_dir)
    assert any("V8" in line and "spl-both.yaml" in line for line in errors)