"""Deterministic checks return facts without setting severity."""

import pytest

from contracts.mock_splunk import Alert, SplunkEvidence
from services.mock_splunk.app import create_app as create_splunk_app
from services.triage.checks import compute_facts
from services.triage.config import load_settings
from services.triage.http_transport import InProcessTransport


@pytest.fixture
def seed_bundle() -> tuple[Alert, list[SplunkEvidence]]:
    app = create_splunk_app()
    from httpx import Client

    with Client(transport=InProcessTransport(app), base_url="http://test") as client:
        client.post("/v1/reset", json={})
        alert = Alert.model_validate(client.get("/v1/alerts/SPL-2026-0001").json())
        evidence = [
            SplunkEvidence.model_validate(item)
            for item in client.get("/v1/alerts/SPL-2026-0001/evidence").json()["items"]
        ]
    return alert, evidence


def test_seed_facts(seed_bundle: tuple[Alert, list[SplunkEvidence]]) -> None:
    alert, evidence = seed_bundle
    patterns = load_settings().routing_config.privileged_account_patterns
    facts = compute_facts(alert, evidence, patterns)
    assert facts["privileged_account"] is True
    assert facts["first_seen_country"] is True
    assert facts["rdp_lateral_hosts"] == ["JMP-01", "FIN-DB-01"]
    assert facts["evidence_count"] == 7
    assert facts["mitre_techniques"] == ["T1078", "T1021"]


def test_checks_do_not_set_severity(seed_bundle: tuple[Alert, list[SplunkEvidence]]) -> None:
    alert, evidence = seed_bundle
    patterns = load_settings().routing_config.privileged_account_patterns
    facts = compute_facts(alert, evidence, patterns)
    assert "severity" not in facts
    assert "assessed_severity" not in facts