"""End-to-end LangGraph triage against in-process services."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

import httpx
import pytest

from services.triage.config import ConfigError, load_settings, require_api_key
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import FixtureGrokClient, GrokResponse, HttpGrokClient


class CountingGrok(FixtureGrokClient):
    def __init__(self, path: Path) -> None:
        super().__init__(path, model_requested="grok-3-mini")
        self.calls = 0

    def assess(self, user_message: str, *, evidence_ids: set[str]) -> GrokResponse:
        self.calls += 1
        return super().assess(user_message, evidence_ids=evidence_ids)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALERT_ID = "SPL-2026-0001"


def test_seed_alert_triaged_end_to_end(triage_stack: TriageDeps) -> None:
    result = run_triage(triage_stack, ALERT_ID)
    assert result.outcome == "triaged"
    assert result.case_id is not None
    case = triage_stack.case_client.get_case(result.case_id)
    assert case.status.value == "TRIAGED"
    assert case.triage_model == "grok-4.3"
    evidence = triage_stack.case_client.list_evidence(result.case_id)
    splunk_refs = [item for item in evidence if item.ref.startswith("EV-")]
    assert len(splunk_refs) == 7
    assessments = [item for item in evidence if item.source == "triage_assessment"]
    assert len(assessments) == 1


def test_audit_order_ingested_evidence_assessment_triaged(triage_stack: TriageDeps) -> None:
    result = run_triage(triage_stack, ALERT_ID)
    case_id = result.case_id
    assert case_id is not None
    response = triage_stack.case_client._client.get(
        "/v1/events",
        params={"case_id": case_id, "after_id": 0, "limit": 200},
    )
    events = response.json()["items"]
    types = [event["type"] for event in events]
    assert types == ["case.ingested", *["evidence.added"] * 7, "evidence.added", "case.triaged"]
    assert events[-1]["type"] == "case.triaged"
    assessment_event = events[-2]
    assert assessment_event["payload"]["source"] == "triage_assessment"


def test_persists_returned_model_not_requested(triage_stack: TriageDeps) -> None:
    result = run_triage(triage_stack, ALERT_ID)
    case = triage_stack.case_client.get_case(result.case_id)  # type: ignore[arg-type]
    assert result.model_requested == "grok-3-mini"
    assert result.model_returned == "grok-4.3"
    assert case.triage_model == "grok-4.3"
    evidence = triage_stack.case_client.list_evidence(case.id)
    assessment = next(item for item in evidence if item.source == "triage_assessment")
    assert assessment.content["model_returned"] == "grok-4.3"
    assert assessment.content["model_requested"] == "grok-3-mini"


def test_persists_response_id_and_request_id(triage_stack: TriageDeps) -> None:
    result = run_triage(triage_stack, ALERT_ID)
    assert result.response_id == "chatcmpl-fixture-seed-0001"
    evidence = triage_stack.case_client.list_evidence(result.case_id)  # type: ignore[arg-type]
    assessment = next(item for item in evidence if item.source == "triage_assessment")
    assert assessment.ref == "chatcmpl-fixture-seed-0001"
    assert assessment.content["response_id"] == "chatcmpl-fixture-seed-0001"


def test_mitre_and_triggered_at_in_evidence_and_rationale(triage_stack: TriageDeps) -> None:
    result = run_triage(triage_stack, ALERT_ID)
    case = triage_stack.case_client.get_case(result.case_id)  # type: ignore[arg-type]
    evidence = triage_stack.case_client.list_evidence(case.id)
    assessment = next(item for item in evidence if item.source == "triage_assessment")
    assert assessment.content["alert_mitre_techniques"] == ["T1078", "T1021"]
    assert assessment.content["alert_triggered_at"] == "2026-11-18T02:04:00Z"
    assert "T1078" in case.triage_rationale
    assert "2026-11-18T02:04:00Z" in case.triage_rationale


def test_second_run_skips_existing_case_without_retriage(triage_stack: TriageDeps) -> None:
    counting = CountingGrok(FIXTURES / "grok_seed_ok.json")
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=counting,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    first = run_triage(deps, ALERT_ID)
    assert first.outcome == "triaged"
    second = run_triage(deps, ALERT_ID)
    assert second.outcome == "skipped_existing"
    assert counting.calls == 1
    response = triage_stack.case_client._client.get(
        f"/v1/events?case_id={first.case_id}&after_id=0&limit=50"
    )
    types = [item["type"] for item in response.json()["items"]]
    assert types.count("case.triaged") == 1


def test_duplicate_logs_ingest_duplicate(
    triage_stack: TriageDeps,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="services.triage.graph")
    run_triage(triage_stack, ALERT_ID)
    with caplog.at_level(logging.INFO, logger="services.triage.graph"):
        run_triage(triage_stack, ALERT_ID)
    assert any("ingest.duplicate" in record.message for record in caplog.records)


def test_resume_from_new_attaches_only_missing_evidence(triage_stack: TriageDeps) -> None:
    from contracts.ingest_mapping import evidence_to_evidence_create
    from services.triage.clients import TRIAGE_ACTOR, ingest_from_alert

    alert = triage_stack.splunk_client.get_alert(ALERT_ID)
    ingest_from_alert(triage_stack.case_client, alert)
    items = triage_stack.splunk_client.list_evidence(ALERT_ID).items
    create = evidence_to_evidence_create(items[0], TRIAGE_ACTOR)
    listed = triage_stack.case_client.list_cases_by_alert_id(ALERT_ID)
    case_id = listed.items[0].id
    triage_stack.case_client.add_evidence(case_id, create)
    before = len(triage_stack.case_client.list_evidence(case_id))
    assert before == 1
    result = run_triage(triage_stack, ALERT_ID)
    assert result.outcome == "triaged"
    evidence = triage_stack.case_client.list_evidence(case_id)
    splunk_rows = [row for row in evidence if row.ref.startswith("EV-")]
    assert len(splunk_rows) == 7


def test_model_call_failure_flags_review(triage_stack: TriageDeps) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "down"})

    grok = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=grok,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    result = run_triage(deps, ALERT_ID)
    assert calls["n"] == 2
    assert result.outcome == "needs_review"
    case = triage_stack.case_client.get_case(result.case_id)  # type: ignore[arg-type]
    assert case.status.value == "NEEDS_REVIEW"
    assert case.triage_severity is None
    failures = triage_stack.case_client.list_evidence(case.id)
    failure = next(item for item in failures if item.source == "triage_failure")
    assert failure.content["reason"] == "model_call_failed"


def test_model_call_timeout_flags_review(triage_stack: TriageDeps) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out")

    grok = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=grok,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    result = run_triage(deps, ALERT_ID)
    assert calls["n"] == 2
    case = triage_stack.case_client.get_case(result.case_id)  # type: ignore[arg-type]
    assert case.status.value == "NEEDS_REVIEW"
    assert case.triage_severity is None
    evidence_rows = triage_stack.case_client.list_evidence(case.id)
    failure = next(item for item in evidence_rows if item.source == "triage_failure")
    assert failure.content["reason"] == "model_call_failed"


def test_needs_review_rerun_skips_without_retriage(triage_stack: TriageDeps) -> None:
    counting = CountingGrok(FIXTURES / "grok_seed_ok.json")
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=counting,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    fail_deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=HttpGrokClient(
            api_key="secret",
            model="grok-3-mini",
            base_url="https://api.x.ai/v1",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(500, json={"error": "down"})
            ),
        ),
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    first = run_triage(fail_deps, ALERT_ID)
    assert first.outcome == "needs_review"
    counting.calls = 0
    second = run_triage(deps, ALERT_ID)
    assert second.outcome == "skipped_existing"
    assert counting.calls == 0


def test_missing_api_key_fails_before_network() -> None:
    settings = load_settings()
    settings = settings.__class__(
        xai_api_key=None,
        xai_model=settings.xai_model,
        xai_base_url=settings.xai_base_url,
        case_api_url=settings.case_api_url,
        mock_splunk_url=settings.mock_splunk_url,
        routing_config=settings.routing_config,
        routing_path=settings.routing_path,
    )
    with pytest.raises(ConfigError):
        require_api_key(settings)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "grok_not_json.json",
        "grok_bad_severity.json",
        "grok_unknown_evidence_ref.json",
        "grok_missing_field.json",
        "grok_missing_response_id.json",
        "grok_missing_model.json",
    ],
)
def test_invalid_output_flags_review(
    triage_stack: TriageDeps,
    fixture_name: str,
) -> None:
    grok = FixtureGrokClient(FIXTURES / fixture_name, model_requested="grok-3-mini")
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=grok,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
    )
    result = run_triage(deps, ALERT_ID)
    assert result.outcome == "needs_review"
    case = triage_stack.case_client.get_case(result.case_id)  # type: ignore[arg-type]
    assert case.status.value == "NEEDS_REVIEW"
    assert case.triage_severity is None
    evidence = triage_stack.case_client.list_evidence(case.id)
    failure = next(item for item in evidence if item.source == "triage_failure")
    assert failure.content["reason"] == "invalid_model_output"


def test_no_network_in_ci() -> None:
    with pytest.raises(OSError, match="network disabled"):
        socket.getaddrinfo("example.com", 80)