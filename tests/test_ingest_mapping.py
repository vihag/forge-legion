"""Mock Splunk records map onto Case API creates, including an in-process ingest."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.ingest_mapping import alert_to_case_create, evidence_to_evidence_create
from contracts.mock_splunk import Alert, Entities, SplunkEvidence
from contracts.models import Actor
from services.case_api.app import create_app as create_case_app
from services.mock_splunk.app import create_app as create_splunk_app
from services.mock_splunk.store import SplunkStore
from tests.mock_splunk.conftest import fixed_clock

ACTOR = Actor(id="splunk-watcher", type="system")
ALERT_ID = "SPL-2026-0001"


def test_alert_maps_to_valid_case_create() -> None:
    alert = SplunkStore(clock=fixed_clock).get_alert(ALERT_ID)
    created = alert_to_case_create(alert, ACTOR)
    assert created.alert_id == alert.alert_id
    assert created.title == alert.title
    assert created.splunk_severity == alert.splunk_severity == "medium"
    assert created.summary == alert.summary
    assert created.assigned_analyst is None
    assert created.actor == ACTOR
    assert created.alert_time == "2026-11-18T02:04:00Z"
    assert set(created.model_dump()) == {
        "alert_id",
        "title",
        "splunk_severity",
        "summary",
        "assigned_analyst",
        "alert_time",
        "actor",
    }


def test_evidence_maps_ref_to_evidence_id() -> None:
    evidence = next(
        item
        # Clock 1 s after the hero's last record so the reset restamp leaves fixture
        # times as-is.
        for item in SplunkStore(
            clock=lambda: datetime(2026, 11, 18, 2, 20, 12, tzinfo=UTC)
        ).loaded_evidence()
        if item.evidence_id == "EV-AUTH-0003"
    )
    created = evidence_to_evidence_create(evidence, ACTOR)
    assert created.source == "auth"
    assert created.ref == evidence.evidence_id == "EV-AUTH-0003"
    assert created.actor == ACTOR
    assert created.content["timestamp"] == "2026-11-18T02:03:41Z"
    assert created.content["alert_ids"] == ["SPL-2026-0001"]
    assert created.content["entities"]["user"] == "adm.mtan"
    assert created.content["entities"]["src_country"] == "RO"
    assert created.content["first_seen_country"] is True
    assert created.content["vpn_ip"] == "10.20.0.5"
    assert created.content["mfa_method"] == "push"


def test_seed_ingests_into_case_api_end_to_end(tmp_path: Path) -> None:
    splunk = create_splunk_app(clock=lambda: datetime(2026, 11, 18, 3, 0, tzinfo=UTC))
    cases = create_case_app(db_path=str(tmp_path / "cases.db"))
    with TestClient(splunk) as splunk_client, TestClient(cases) as case_client:
        alert_response = splunk_client.get(f"/v1/alerts/{ALERT_ID}")
        assert alert_response.status_code == 200
        alert = Alert.model_validate(alert_response.json())
        created = case_client.post(
            "/v1/cases",
            json=alert_to_case_create(alert, ACTOR).model_dump(mode="json"),
        )
        assert created.status_code == 201
        case = created.json()
        assert case["alert_id"] == ALERT_ID
        assert case["title"] == alert.title
        assert case["splunk_severity"] == "medium"
        assert case["summary"] == alert.summary
        assert case["assigned_analyst"] is None
        assert case["status"] == "NEW"
        case_id = case["id"]

        evidence_response = splunk_client.get(f"/v1/alerts/{ALERT_ID}/evidence")
        items = [
            item
            for item in evidence_response.json()["items"]
            if item["source"] in {"auth", "proxy", "edr"}
        ]
        assert len(items) == 7
        refs: list[str] = []
        for item in items:
            evidence = SplunkEvidence.model_validate(item)
            body = evidence_to_evidence_create(evidence, ACTOR).model_dump(mode="json")
            response = case_client.post(f"/v1/cases/{case_id}/evidence", json=body)
            assert response.status_code == 201, response.text
            assert response.json()["ref"] == evidence.evidence_id
            assert response.json()["source"] == evidence.source
            refs.append(evidence.evidence_id)

        history = case_client.get(f"/v1/cases/{case_id}/history")
        assert history.status_code == 200
        types = [event["type"] for event in history.json()]
        assert types == ["case.ingested", *["evidence.added"] * 7]
        assert types.count("case.ingested") == 1
        assert types.count("evidence.added") == 7
        stored = case_client.get(f"/v1/cases/{case_id}/evidence").json()
        assert [item["ref"] for item in stored] == refs


def test_content_key_collision_raises() -> None:
    evidence = SplunkEvidence(
        evidence_id="EV-AUTH-0009",
        source="auth",
        timestamp=datetime(2026, 11, 18, 2, 4, tzinfo=UTC),
        alert_ids=[ALERT_ID],
        entities=Entities(user="adm.mtan"),
        content={"timestamp": "2026-11-18T02:04:00Z", "entities": {"user": "other"}},
    )
    with pytest.raises(ValueError, match="alert_ids|entities|timestamp"):
        evidence_to_evidence_create(evidence, ACTOR)
