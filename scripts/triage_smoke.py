"""Live triage smoke: in-process apps, one Grok call, JSON TriageResult on stdout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from services.case_api.app import create_app as create_case_app
from services.mock_splunk.app import create_app as create_splunk_app
from services.triage.clients import CaseApiClient, SplunkClient
from services.triage.config import ConfigError, load_settings, require_api_key
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import HttpGrokClient
from services.triage.http_transport import InProcessTransport

ALERT_ID = "SPL-2026-0001"
ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "triage" / "fixtures" / "grok_seed_ok.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--record",
        action="store_true",
        help="Write tests/triage/fixtures/grok_seed_ok.json from live Grok",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        api_key = require_api_key(settings)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        case_app = create_case_app(db_path=str(Path(tmp) / "cases.db"))
        splunk_app = create_splunk_app()
        case_client = CaseApiClient("http://case", transport=InProcessTransport(case_app))
        splunk_client = SplunkClient("http://splunk", transport=InProcessTransport(splunk_app))
        splunk_client.reset()
        grok_client = HttpGrokClient(
            api_key=api_key,
            model=settings.xai_model,
            base_url=settings.xai_base_url,
            live=True,
        )
        deps = TriageDeps(
            case_client=case_client,
            splunk_client=splunk_client,
            grok_client=grok_client,
            routing=settings.routing_config,
            model_requested=settings.xai_model,
        )
        try:
            if args.record:
                _record_grok(grok_client, splunk_client)
                return 0
            result = run_triage(deps, ALERT_ID)
        finally:
            case_client.close()
            splunk_client.close()
            grok_client.close()

    print(json.dumps(result.model_dump(mode="json"), sort_keys=True, indent=2))
    print(f"model_requested={result.model_requested}", file=sys.stderr)
    print(f"model_returned={result.model_returned}", file=sys.stderr)
    print(f"response_id={result.response_id}", file=sys.stderr)
    return 0


def _record_grok(grok_client: HttpGrokClient, splunk_client: SplunkClient) -> None:
    from contracts.triage import GrokAssessment
    from services.triage.checks import compute_facts
    from services.triage.config import load_settings
    from services.triage.prompt import build_user_message

    alert = splunk_client.get_alert(ALERT_ID)
    evidence = splunk_client.list_evidence(ALERT_ID).items
    patterns = load_settings().routing_config.privileged_account_patterns
    facts = compute_facts(alert, evidence, patterns)
    payload = [
        {
            "evidence_id": item.evidence_id,
            "source": item.source,
            "timestamp": item.timestamp.isoformat(),
            "content": item.content,
        }
        for item in evidence
    ]
    message = build_user_message(alert, facts, payload)
    raw = grok_client.assess(message, evidence_ids={item.evidence_id for item in evidence})
    GrokAssessment.model_validate_json(raw.content)
    body = {
        "id": raw.response_id,
        "model": raw.model_returned,
        "choices": [{"message": {"role": "assistant", "content": raw.content}}],
        "usage": raw.usage,
    }
    FIXTURE_PATH.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE_PATH}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())