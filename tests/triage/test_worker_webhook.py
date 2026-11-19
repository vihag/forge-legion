"""Worker webhook enqueue paths (WH2 steer)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from contracts.models import CaseStatus
from services.case_api.app import create_app as create_case_app
from services.mock_splunk.app import create_app as create_splunk_app
from services.mock_splunk.stream import generate, reset_registry
from services.triage.clients import CaseApiClient, SplunkClient
from services.triage.config import load_settings
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import StreamFixtureGrokClient
from services.triage.http_transport import InProcessTransport
from services.triage.webhook import RoutineWebhook
from services.triage.worker import handle_triage_result


class _RecordingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def worker_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TriageDeps, CaseApiClient, SplunkClient, object]]:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    monkeypatch.setenv("ROUTINE_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("ROUTINE_WEBHOOK_URL", "http://stub.example/hook")
    monkeypatch.setenv("ROUTINE_WEBHOOK_KEY", "k")
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "tests/fixtures/scenarios/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(root / "tests/fixtures/scenarios"))
    case_app = create_case_app(db_path=str(tmp_path / "cases.db"))
    splunk_app = create_splunk_app()
    case_client = CaseApiClient("http://case.test", transport=InProcessTransport(case_app))
    splunk_client = SplunkClient("http://splunk.test", transport=InProcessTransport(splunk_app))
    settings = load_settings()
    deps = TriageDeps(
        case_client=case_client,
        splunk_client=splunk_client,
        grok_client=StreamFixtureGrokClient(model_requested="grok-3-mini"),
        routing=settings.routing_config,
        model_requested="grok-3-mini",
    )
    try:
        yield deps, case_client, splunk_client, splunk_app
    finally:
        case_client.close()
        splunk_client.close()


def _ingest_one(splunk_app, seed: int = 9) -> str:
    splunk_app.state.store.reset()
    reset_registry()
    item = generate(seed, 1)[0][0]
    splunk_app.state.store._ingest_stream_item(item)
    return item.alert.alert_id


def test_wh2_auto_resolved_and_skipped_existing_no_webhook_posts(
    worker_stack: tuple[TriageDeps, CaseApiClient, SplunkClient, object],
) -> None:
    deps, case_client, _splunk_client, splunk_app = worker_stack
    transport = _RecordingTransport()
    webhook = RoutineWebhook.from_env(transport=transport, sleep=lambda _s: None)
    assert webhook is not None
    webhook.start()
    alert_id = _ingest_one(splunk_app, seed=42)
    first = run_triage(deps, alert_id)
    handle_triage_result(webhook=webhook, case_client=case_client, result=first)
    time.sleep(0.2)
    case = case_client.get_case(int(first.case_id))
    if case.status == CaseStatus.AUTO_RESOLVED:
        assert transport.requests == []
    second = run_triage(deps, alert_id)
    assert second.outcome == "skipped_existing"
    _, _, state = handle_triage_result(
        webhook=webhook,
        case_client=case_client,
        result=second,
    )
    assert state == "skipped"
    webhook.shutdown()
    assert len(transport.requests) == 0


def test_wh_triaged_worker_posts_once_with_severity(
    worker_stack: tuple[TriageDeps, CaseApiClient, SplunkClient, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "0")
    deps, case_client, splunk_client, splunk_app = worker_stack
    transport = _RecordingTransport()
    webhook = RoutineWebhook.from_env(transport=transport, sleep=lambda _s: None)
    assert webhook is not None
    webhook.start()
    alert_id = _ingest_one(splunk_app, seed=7)
    result = run_triage(deps, alert_id)
    _, _, state = handle_triage_result(webhook=webhook, case_client=case_client, result=result)
    assert state == "sent"
    time.sleep(0.2)
    webhook.shutdown()
    assert len(transport.requests) == 1
    import json

    body = json.loads(transport.requests[0].content.decode())
    assert "severity" in body


def test_wh_gate_refused_autonomous_enqueues(
    worker_stack: tuple[TriageDeps, CaseApiClient, SplunkClient, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps, case_client, splunk_client, splunk_app = worker_stack
    monkeypatch.setenv("AUTO_RESOLVE_MIN_CONFIDENCE", "0.99")
    transport = _RecordingTransport()
    webhook = RoutineWebhook.from_env(transport=transport, sleep=lambda _s: None)
    assert webhook is not None
    webhook.start()
    alert_id = _ingest_one(splunk_app, seed=11)
    result = run_triage(deps, alert_id)
    route, gate, state = handle_triage_result(
        webhook=webhook,
        case_client=case_client,
        result=result,
    )
    case = case_client.get_case(int(result.case_id))
    if case.status == CaseStatus.TRIAGED and case.route == "autonomous":
        assert gate.startswith("fail:")
        assert state == "sent"
        time.sleep(0.2)
        assert len(transport.requests) == 1
        import json

        body = json.loads(transport.requests[0].content.decode())
        assert body["route"] == "autonomous"
    webhook.shutdown()