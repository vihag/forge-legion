"""Worker logging configuration (C8 steer)."""

from __future__ import annotations

import logging

from services.triage.worker import configure_worker_logging


def test_worker_configures_info_logging(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def _basic_config(**kwargs: object) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(logging, "basicConfig", _basic_config)
    configure_worker_logging()
    assert captured
    assert captured[0].get("level") == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING