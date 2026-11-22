"""Continuous triage worker: poll Mock Splunk and run LangGraph triage."""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Literal

from contracts.models import Case, CaseStatus
from contracts.triage import TriageResult
from services.triage.clients import AlertNotFound, CaseApiClient, SplunkClient
from services.triage.config import TriageSettings, load_settings
from services.triage.graph import TriageDeps
from services.triage.grok import (
    FixtureGrokClient,
    GrokClient,
    HttpGrokClient,
    StreamFixtureGrokClient,
)
from services.triage.tracing import build_tracer, shutdown_tracer_once, triage_with_trace
from services.triage.webhook import RoutineWebhook, WebhookPayload

logger = logging.getLogger(__name__)

WebhookState = Literal["sent", "skipped", "disabled"]


def _resolve_grok_client(settings: TriageSettings) -> GrokClient:
    if settings.grok_client == "fixture":
        if settings.grok_fixture_path is not None:
            return FixtureGrokClient(
                settings.grok_fixture_path,
                model_requested=settings.xai_model,
            )
        return StreamFixtureGrokClient(model_requested=settings.xai_model)
    from services.triage.config import require_api_key

    api_key = require_api_key(settings)
    return HttpGrokClient(
        api_key=api_key,
        model=settings.xai_model,
        base_url=settings.xai_base_url,
    )


def _validate_concurrency() -> int:
    raw = os.environ.get("TRIAGE_WORKER_CONCURRENCY", "1").strip()
    try:
        value = int(raw)
    except ValueError:
        print(f"invalid TRIAGE_WORKER_CONCURRENCY {raw!r}", file=sys.stderr)
        raise SystemExit(2) from None
    if value not in (1, 2):
        print(f"TRIAGE_WORKER_CONCURRENCY must be 1 or 2, got {value}", file=sys.stderr)
        raise SystemExit(2)
    return value


def _gate_label(case: Case, result: TriageResult) -> str:
    if result.route != "autonomous":
        return "pass"
    if case.status == CaseStatus.AUTO_RESOLVED:
        return "pass"
    rule = case.rule or result.rule or "unknown"
    return f"fail:{rule}"


def _webhook_payload(case: Case, case_id: int) -> WebhookPayload:
    triage_sev = case.triage_severity
    splunk_sev = case.splunk_severity
    severity = triage_sev if triage_sev is not None else splunk_sev
    route = case.route if case.route is not None else "collaborative"
    rule = case.rule or ""
    return WebhookPayload(
        case_id=case_id,
        alert_id=case.alert_id,
        severity=severity,
        route=route,
        rule=rule,
    )


def handle_triage_result(
    *,
    webhook: RoutineWebhook | None,
    case_client: CaseApiClient,
    result: TriageResult,
) -> tuple[str, str, WebhookState]:
    """Apply webhook rules for one triage outcome. Returns route, gate, webhook state."""
    route = result.route or "—"
    gate = "—"
    webhook_state: WebhookState = "disabled" if webhook is None else "skipped"
    if result.case_id is None or result.outcome == "skipped_existing":
        return route, gate, webhook_state
    case = case_client.get_case(result.case_id)
    route = case.route if case.route is not None else route
    gate = _gate_label(case, result)
    if webhook is None:
        return route, gate, "disabled"
    if case.status != CaseStatus.TRIAGED:
        return route, gate, "skipped"
    webhook.enqueue(_webhook_payload(case, result.case_id))
    return route, gate, "sent"


def configure_worker_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def run_worker() -> int:
    configure_worker_logging()
    _validate_concurrency()
    poll_s = int(os.environ.get("TRIAGE_WORKER_POLL_S", "5"))
    settings = load_settings()
    grok_client = _resolve_grok_client(settings)
    case_client = CaseApiClient(settings.case_api_url)
    splunk_client = SplunkClient(settings.mock_splunk_url)
    tracer = build_tracer(os.environ)
    deps = TriageDeps(
        case_client=case_client,
        splunk_client=splunk_client,
        grok_client=grok_client,
        routing=settings.routing_config,
        model_requested=settings.xai_model,
        tracer=tracer,
    )
    webhook = RoutineWebhook.from_env()
    if webhook is not None:
        webhook.start()
    after_seq = 0
    try:
        while True:
            page = splunk_client.list_alerts(after_seq=after_seq, limit=100)
            for alert in page.items:
                try:

                    def _handle(result: TriageResult) -> tuple[str, str, WebhookState]:
                        return handle_triage_result(
                            webhook=webhook,
                            case_client=case_client,
                            result=result,
                        )

                    result, route, gate, webhook_state = triage_with_trace(
                        tracer=tracer,
                        deps=deps,
                        alert_id=alert.alert_id,
                        handle_result=_handle,
                    )
                    logger.info(
                        "alert_id=%s case_id=%s route=%s gate=%s webhook=%s",
                        alert.alert_id,
                        result.case_id,
                        route,
                        gate,
                        webhook_state,
                    )
                except AlertNotFound:
                    logger.warning("alert missing during triage alert_id=%s", alert.alert_id)
            after_seq = page.next_after_seq
            time.sleep(poll_s)
    except KeyboardInterrupt:
        return 0
    finally:
        if webhook is not None:
            webhook.shutdown()
        flush_raw = os.environ.get("LANGFUSE_FLUSH_TIMEOUT_S", "5").strip() or "5"
        try:
            flush_timeout = float(flush_raw)
        except ValueError:
            flush_timeout = 5.0
        shutdown_tracer_once(tracer, timeout_s=flush_timeout)
        case_client.close()
        splunk_client.close()
        grok_client.close()