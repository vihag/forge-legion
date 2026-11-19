"""C7 P8: triage service must not import SOAR modules."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TRIAGE_ROOT = ROOT / "services" / "triage"


def _iter_python_modules() -> list[Path]:
    return sorted(path for path in TRIAGE_ROOT.rglob("*.py") if path.is_file())


def test_p8_no_soar_imports_in_triage_package() -> None:
    forbidden = ("services.case_api.soar", "services.mock_soar")
    for path in _iter_python_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.startswith(prefix) for prefix in forbidden), (
                        f"{path}: import {alias.name}"
                    )
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(prefix) for prefix in forbidden), (
                    f"{path}: from {node.module}"
                )


def test_p8_triage_config_has_no_soar_url() -> None:
    content = (ROOT / "services" / "triage" / "config.py").read_text(encoding="utf-8")
    assert "MOCK_SOAR_URL" not in content
    routing = yaml.safe_load((ROOT / "config" / "routing.yaml").read_text(encoding="utf-8"))
    dumped = yaml.safe_dump(routing)
    assert "soar" not in dumped.casefold()