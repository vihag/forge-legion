"""Scenario validation (SC1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_scenarios_check_valid_fixtures() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.mock_splunk.scenarios",
            "check",
            "--world",
            "tests/fixtures/scenarios/world.yaml",
            "--dir",
            "tests/fixtures/scenarios",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_scenarios_check_invalid_fixture() -> None:
    invalid = Path("tests/fixtures/scenarios_invalid/bad-auto-host.yaml")
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text(
        """id: bad-auto-host
title: bad
splunk_rule_name: bad
splunk_severity: low
outcome_class: auto_benign
weight: 1
entities:
  user:
    ref: user:adm.mtan
alert:
  title: t
  description: d
  fields: {}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.mock_splunk.scenarios",
            "check",
            "--world",
            "tests/fixtures/scenarios/world.yaml",
            "--dir",
            "tests/fixtures/scenarios_invalid",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "bad-auto-host" in result.stderr