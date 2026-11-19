"""Prompt builder is deterministic and free of scenario answers."""

from httpx import Client

from contracts.mock_splunk import Alert, SplunkEvidence
from services.mock_splunk.app import create_app as create_splunk_app
from services.triage.checks import compute_facts
from services.triage.config import load_settings
from services.triage.http_transport import InProcessTransport
from services.triage.prompt import SYSTEM_PROMPT, build_user_message


def _seed() -> tuple[Alert, list[dict[str, object]], dict[str, object]]:
    app = create_splunk_app()
    with Client(transport=InProcessTransport(app), base_url="http://test") as client:
        client.post("/v1/reset", json={})
        alert = Alert.model_validate(client.get("/v1/alerts/SPL-2026-0001").json())
        evidence = [
            SplunkEvidence.model_validate(item)
            for item in client.get("/v1/alerts/SPL-2026-0001/evidence").json()["items"]
        ]
    patterns = load_settings().routing_config.privileged_account_patterns
    facts = compute_facts(alert, evidence, patterns)
    evidence_payload = [
        {
            "evidence_id": item.evidence_id,
            "source": item.source,
            "timestamp": item.timestamp.isoformat(),
            "content": item.content,
        }
        for item in evidence
    ]
    return alert, evidence_payload, facts


def test_user_message_is_deterministic_and_lists_evidence_ids() -> None:
    alert, evidence_payload, facts = _seed()
    first = build_user_message(alert, facts, evidence_payload)
    second = build_user_message(alert, facts, evidence_payload)
    assert first == second
    for item in evidence_payload:
        assert item["evidence_id"] in first


def test_prompt_contains_no_expected_answer() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "verdict: high" not in lowered
    assert "verdict: critical" not in lowered
    assert "answer is high" not in lowered
    assert "answer is critical" not in lowered