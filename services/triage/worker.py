"""Continuous triage worker: poll Mock Splunk and run LangGraph triage."""

from __future__ import annotations

import logging
import os
import sys
import time

from services.triage.clients import AlertNotFound, CaseApiClient, SplunkClient
from services.triage.config import TriageSettings, load_settings
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import (
    FixtureGrokClient,
    GrokClient,
    HttpGrokClient,
    StreamFixtureGrokClient,
)
from services.triage.webhook import RoutineWebhook, WebhookPayload

logger = logging.getLogger(__name__)


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


def run_worker() -> int:
    _validate_concurrency()
    poll_s = int(os.environ.get("TRIAGE_WORKER_POLL_S", "5"))
    settings = load_settings()
    grok_client = _resolve_grok_client(settings)
    case_client = CaseApiClient(settings.case_api_url)
    splunk_client = SplunkClient(settings.mock_splunk_url)
    deps = TriageDeps(
        case_client=case_client,
        splunk_client=splunk_client,
        grok_client=grok_client,
        routing=settings.routing_config,
        model_requested=settings.xai_model,
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
                    result = run_triage(deps, alert.alert_id)
                    logger.info(
                        "triage alert_id=%s outcome=%s case_id=%s",
                        alert.alert_id,
                        result.outcome,
                        result.case_id,
                    )
                    if (
                        webhook is not None
                        and result.case_id is not None
                        and result.outcome != "skipped_existing"
                    ):
                        case = case_client.get_case(result.case_id)
                        if case.status.value == "TRIAGED":
                            triage_sev = case.triage_severity
                            splunk_sev = case.splunk_severity
                            severity = (
                                triage_sev
                                if triage_sev is not None
                                else splunk_sev
                            )
                            route = case.route if case.route is not None else "collaborative"
                            rule = case.rule or ""
                            webhook.enqueue(
                                WebhookPayload(
                                    case_id=result.case_id,
                                    alert_id=case.alert_id,
                                    severity=severity,
                                    route=route,
                                    rule=rule,
                                )
                            )
                except AlertNotFound:
                    logger.warning("alert missing during triage: %s", alert.alert_id)
            after_seq = page.next_after_seq
            time.sleep(poll_s)
    except KeyboardInterrupt:
        return 0
    finally:
        if webhook is not None:
            webhook.shutdown()
        case_client.close()
        splunk_client.close()
        grok_client.close()