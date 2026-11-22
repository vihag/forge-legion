"""CLI entry: python -m services.triage run --alert-id <id>."""

import argparse
import json
import os
import sys
from typing import Never

from services.triage.clients import AlertNotFound, CaseApiClient, SplunkClient
from services.triage.config import ConfigError, TriageSettings, load_settings, require_api_key
from services.triage.graph import TriageDeps
from services.triage.grok import (
    FixtureGrokClient,
    GrokClient,
    HttpGrokClient,
)
from services.triage.tracing import build_tracer, shutdown_tracer_once, triage_with_trace
from services.triage.worker import handle_triage_result, run_worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="services.triage")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run triage for one alert id")
    run.add_argument("--alert-id", required=True)
    sub.add_parser("worker", help="Poll Mock Splunk and triage new alerts")
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run_alert(args.alert_id)
    if args.command == "worker":
        return run_worker()
    _never(args.command)


def _resolve_grok_client(settings: TriageSettings) -> GrokClient | tuple[int, str]:
    if settings.grok_client == "fixture":
        print("grok_client=fixture", file=sys.stderr)
        if settings.grok_fixture_path is None:
            return 2, "GROK_FIXTURE_PATH is not set"
        return FixtureGrokClient(settings.grok_fixture_path, model_requested=settings.xai_model)
    if settings.grok_client != "http":
        return 2, f"unknown GROK_CLIENT {settings.grok_client!r} (expected http or fixture)"
    try:
        api_key = require_api_key(settings)
    except ConfigError as exc:
        return 2, str(exc)
    return HttpGrokClient(
        api_key=api_key,
        model=settings.xai_model,
        base_url=settings.xai_base_url,
    )


def _run_alert(alert_id: str) -> int:
    settings = load_settings()
    grok_or_error = _resolve_grok_client(settings)
    if isinstance(grok_or_error, tuple):
        code, message = grok_or_error
        print(message, file=sys.stderr)
        return code
    grok_client = grok_or_error
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
    exit_code = 0
    result = None
    try:

        def _handle(res):  # type: ignore[no-untyped-def]
            return handle_triage_result(
                webhook=None,
                case_client=case_client,
                result=res,
            )

        result, _, _ = triage_with_trace(
            tracer=tracer,
            deps=deps,
            alert_id=alert_id,
            handle_result=_handle,
        )[:3]
    except AlertNotFound as exc:
        print(str(exc), file=sys.stderr)
        exit_code = 1
    finally:
        flush_raw = os.environ.get("LANGFUSE_FLUSH_TIMEOUT_S", "5").strip() or "5"
        try:
            flush_timeout = float(flush_raw)
        except ValueError:
            flush_timeout = 5.0
        shutdown_tracer_once(tracer, timeout_s=flush_timeout)
        case_client.close()
        splunk_client.close()
        grok_client.close()
    if exit_code == 0 and result is not None:
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return exit_code


def _never(value: str) -> Never:
    raise RuntimeError(f"unknown command {value}")


if __name__ == "__main__":
    raise SystemExit(main())