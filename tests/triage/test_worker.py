"""Continuous triage worker (C7 W1–W3)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.mock_splunk.stream import generate, reset_registry
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import StreamFixtureGrokClient


class CountingStreamGrok(StreamFixtureGrokClient):
    def __init__(self) -> None:
        super().__init__(model_requested="grok-3-mini")
        self.calls = 0

    def assess(self, user_message: str, *, evidence_ids: set[str]):
        self.calls += 1
        return super().assess(user_message, evidence_ids=evidence_ids)


def test_w1_duplicate_alert_triaged_once(
    triage_stack: TriageDeps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.mock_splunk.app import create_app as create_splunk_app
    from services.triage.clients import SplunkClient
    from services.triage.http_transport import InProcessTransport

    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "0")
    splunk_app = create_splunk_app()
    transport = InProcessTransport(splunk_app)
    splunk_client = SplunkClient("http://splunk.test", transport=transport)
    splunk_client.reset()
    reset_registry()
    item = generate(7, 1)[0][0]
    splunk_app.state.store._ingest_stream_item(item)
    counting = CountingStreamGrok()
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=splunk_client,
        grok_client=counting,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    first = run_triage(deps, item.alert.alert_id)
    second = run_triage(deps, item.alert.alert_id)
    assert first.outcome == "triaged"
    assert second.outcome == "skipped_existing"
    assert counting.calls == 1
    listed = triage_stack.case_client.list_cases_by_alert_id(item.alert.alert_id)
    assert listed.total == 1
    case_id = listed.items[0].id
    events = triage_stack.case_client._client.get(
        "/v1/events",
        params={"case_id": case_id, "after_id": 0, "limit": 200},
    ).json()["items"]
    assert sum(1 for event in events if event["type"] == "case.triaged") == 1


def test_w2_concurrency_out_of_range_exits_2() -> None:
    env = {**os.environ, "TRIAGE_WORKER_CONCURRENCY": "3"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from services.triage.worker import _validate_concurrency; _validate_concurrency()",
        ],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert result.returncode == 2


def test_w3_usage_and_model_requested_visible_in_view(triage_stack: TriageDeps) -> None:
    from services.mock_splunk.app import create_app as create_splunk_app
    from services.triage.clients import SplunkClient
    from services.triage.http_transport import InProcessTransport

    splunk_app = create_splunk_app()
    splunk_client = SplunkClient("http://splunk.test", transport=InProcessTransport(splunk_app))
    splunk_client.reset()
    reset_registry()
    item = generate(11, 1)[0][0]
    splunk_app.state.store._ingest_stream_item(item)
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=splunk_client,
        grok_client=StreamFixtureGrokClient(model_requested="grok-3-mini"),
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    result = run_triage(deps, item.alert.alert_id)
    assert result.case_id is not None
    view = triage_stack.case_client.get_case_view(result.case_id)
    assert view.model_requested == "grok-3-mini"
    assert view.prompt_tokens is not None
    assert view.completion_tokens is not None
    case = triage_stack.case_client.get_case(result.case_id)
    assert case.triage_model == "fixture"