"""C8 sources and env QA (SR1, ES1, EM1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from contracts.scenarios import EvidenceSource
from services.mock_splunk.app import create_app
from services.mock_splunk.sources import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
EXPECTED: tuple[EvidenceSource, ...] = (
    "auth",
    "vpn",
    "proxy",
    "edr",
    "firewall",
    "dns",
    "email",
    "cloud",
)


def test_sr1_sources_registry_and_endpoint() -> None:
    assert tuple(sorted(REGISTRY)) == tuple(sorted(EXPECTED))
    client = TestClient(create_app())
    names = [item["name"] for item in client.get("/v1/sources").json()["items"]]
    assert names == list(REGISTRY.keys())
    # Scenario validator accepts the same literal set.
    assert set(EXPECTED) == set(__import__("typing").get_args(EvidenceSource))


def test_es1_env_set_refuses_webhook_secrets(tmp_path: Path) -> None:
    target = tmp_path / ".env.local"
    target.write_text("ROUTINE_WEBHOOK_KEY=secret\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/env_merge.py"),
            "--env-file",
            str(target),
            "--set",
            "ROUTINE_WEBHOOK_KEY",
            "--value",
            "x",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "ROUTINE_WEBHOOK_KEY" in proc.stderr
    proc2 = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/env_merge.py"),
            "--env-file",
            str(target),
            "--set",
            "ROUTINE_WEBHOOK_URL",
            "--value",
            "http://x",
        ],
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 2
    assert "ROUTINE_WEBHOOK_URL" in proc2.stderr


def test_em1_env_merge_preserves_webhook_secrets(tmp_path: Path) -> None:
    target = tmp_path / ".env.local"
    before = (
        "ROUTINE_WEBHOOK_URL=http://keep-url\n"
        "ROUTINE_WEBHOOK_KEY=keep-key\n"
        "OTHER=1\n"
    )
    target.write_text(before, encoding="utf-8")
    lines = before.splitlines()
    existing_keys = {line.split("=", 1)[0] for line in lines if "=" in line}
    additions = {
        "ROUTINE_WEBHOOK_ENABLED": "0",
        "ROUTINE_WEBHOOK_URL": "",
        "ROUTINE_WEBHOOK_KEY": "",
    }
    for key, value in additions.items():
        if key in existing_keys:
            continue
        lines.append(f"{key}={value}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    body = target.read_text(encoding="utf-8")
    assert "ROUTINE_WEBHOOK_URL=http://keep-url" in body
    assert "ROUTINE_WEBHOOK_KEY=keep-key" in body