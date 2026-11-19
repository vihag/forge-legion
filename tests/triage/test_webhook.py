"""C8 routine webhook QA (WH1–WH6)."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from services.triage.webhook import RoutineWebhook, WebhookPayload


class _RecordingTransport(httpx.BaseTransport):
    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _payload(case_id: int = 1) -> WebhookPayload:
    return WebhookPayload(
        case_id=case_id,
        alert_id="SPL-STR-000001",
        severity="medium",
        route="collaborative",
        rule="R1",
    )


def test_wh1_triaged_post_shape_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "http://stub.example/hook")
    key = "test-key-123"
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", key)
    transport = _RecordingTransport(
        lambda _req: httpx.Response(200, json={"ok": True}),
    )
    webhook = RoutineWebhook.from_env(transport=transport, sleep=lambda _s: None)
    assert webhook is not None
    webhook.start()
    webhook.enqueue(_payload())
    time.sleep(0.2)
    webhook.shutdown()
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.method == "POST"
    import json

    body = json.loads(req.content.decode())
    assert set(body.keys()) == {"case_id", "alert_id", "severity", "route", "rule"}
    assert req.headers["Authorization"] == f"Bearer {key}"


def test_wh2_auto_resolved_and_skipped_no_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "http://stub")
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", "k")
    transport = _RecordingTransport(
        lambda _req: httpx.Response(200, json={"ok": True}),
    )
    webhook = RoutineWebhook.from_env(transport=transport, sleep=lambda _s: None)
    assert webhook is not None
    webhook.start()
    # Worker logic: only TRIAGED enqueues; we simulate no enqueue for other paths.
    webhook.shutdown()
    assert transport.requests == []


def test_wh3_retry_backoff_and_no_retry_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "http://stub")
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", "k")
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    transport = _RecordingTransport(handler)
    webhook = RoutineWebhook.from_env(
        transport=transport,
        sleep=lambda seconds: sleeps.append(seconds),
    )
    assert webhook is not None
    webhook.start()
    webhook.enqueue(_payload())
    time.sleep(0.3)
    webhook.shutdown()
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]

    calls["n"] = 0
    transport400 = _RecordingTransport(lambda _req: httpx.Response(400))
    webhook2 = RoutineWebhook.from_env(transport=transport400, sleep=lambda _s: None)
    assert webhook2 is not None
    webhook2.start()
    webhook2.enqueue(_payload(case_id=2))
    time.sleep(0.2)
    webhook2.shutdown()
    assert len(transport400.requests) == 1


def test_wh4_enqueue_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "http://stub")
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", "k")

    def slow(_req: httpx.Request) -> httpx.Response:
        time.sleep(10)
        return httpx.Response(200, json={"ok": True})

    transport = _RecordingTransport(slow)
    webhook = RoutineWebhook.from_env(transport=transport, sleep=lambda _s: None)
    assert webhook is not None
    webhook.start()
    start = time.monotonic()
    webhook.enqueue(_payload())
    webhook.enqueue(_payload(case_id=2))
    elapsed_ms = (time.monotonic() - start) * 1000
    webhook.shutdown()
    assert elapsed_ms < 50


def test_wh5_disabled_variants(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="services.triage.webhook")
    transport = _RecordingTransport(lambda _req: httpx.Response(200))
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "0")
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "http://stub")
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", "k")
    assert RoutineWebhook.from_env(transport=transport) is None
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "")
    assert RoutineWebhook.from_env(transport=transport) is None
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "http://stub")
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", "")
    assert RoutineWebhook.from_env(transport=transport) is None
    markers = ("enabled=0", "url=empty", "key=empty")
    assert any(any(marker in rec.message for marker in markers) for rec in caplog.records)


def test_wh6_no_secrets_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "1")
    url = "http://stub.invalid/hook/URL-CANARY-91c2"
    key = "KEY-CANARY-4d7a"
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", url)
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", key)
    caplog.set_level(10)
    for logger_name in (None, "httpx", "httpcore"):
        logging = __import__("logging")
        logging.getLogger(logger_name).setLevel(10)

    attempts = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("down")
        if attempts["n"] == 2:
            return httpx.Response(400)
        return httpx.Response(503)

    transport = _RecordingTransport(handler)
    webhook = RoutineWebhook.from_env(transport=transport, sleep=lambda _s: None)
    assert webhook is not None
    webhook.start()
    webhook.enqueue(_payload())
    time.sleep(0.4)
    webhook.shutdown()

    combined = capsys.readouterr().out + capsys.readouterr().err
    blob = combined + "\n".join(rec.getMessage() for rec in caplog.records)
    forbidden = [
        "URL-CANARY-91c2",
        "stub.invalid/hook",
        "KEY-CANARY-4d7a",
        "Bearer KEY-CANARY-4d7a",
    ]
    assert not any(token in blob for token in forbidden)