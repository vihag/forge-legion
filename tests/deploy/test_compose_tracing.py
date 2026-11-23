"""C10 deploy scope: tracing extra only on triage images (TR9)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_tracing_scope() -> None:
    compose = yaml.safe_load((ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8"))
    tracing_services: list[str] = []
    for name, svc in compose["services"].items():
        build = svc.get("build") or {}
        args = build.get("args") or {}
        if args.get("PIP_EXTRAS") == "tracing":
            tracing_services.append(name)
        env_file = svc.get("env_file") or []
        assert all("LANGFUSE" not in str(item) for item in env_file)
    assert set(tracing_services) == {"triage_worker", "triage"}
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.startswith("LANGFUSE_") and not line.startswith("# "):
            raise AssertionError(f"uncommented LANGFUSE line: {line}")
    names = [
        "# LANGFUSE_ENABLED=",
        "# LANGFUSE_PUBLIC_KEY=",
        "# LANGFUSE_SECRET_KEY=",
        "# LANGFUSE_HOST=",
        "# LANGFUSE_FLUSH_TIMEOUT_S=5",
    ]
    for name in names:
        assert name in example
    assert "tracing is on when LANGFUSE_PUBLIC_KEY" in example
    assert "Set LANGFUSE_ENABLED=0 to disable" in example
    assert "# LANGFUSE_ENABLED=0" not in example
