"""CLI entry: python -m services.triage run --alert-id <id>."""

import argparse
import json
import sys
from typing import Never

from services.triage.clients import AlertNotFound, CaseApiClient, SplunkClient
from services.triage.config import ConfigError, TriageSettings, load_settings, require_api_key
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import FixtureGrokClient, GrokClient, HttpGrokClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="services.triage")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run triage for one alert id")
    run.add_argument("--alert-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run_alert(args.alert_id)
    _never(args.command)


def _resolve_grok_client(settings: TriageSettings) -> GrokClient | tuple[int, str]:
    if settings.grok_client == "fixture":
        print("grok_client=fixture", file=sys.stderr)
        if settings.grok_fixture_path is None:
            return 2, "GROK_FIXTURE_PATH is not set"
        return FixtureGrokClient(settings.grok_fixture_path, model_requested=settings.xai_model)
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
    deps = TriageDeps(
        case_client=case_client,
        splunk_client=splunk_client,
        grok_client=grok_client,
        routing=settings.routing_config,
        model_requested=settings.xai_model,
    )
    exit_code = 0
    result = None
    try:
        result = run_triage(deps, alert_id)
    except AlertNotFound as exc:
        print(str(exc), file=sys.stderr)
        exit_code = 1
    finally:
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