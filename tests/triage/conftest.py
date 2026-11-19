"""In-process Case API and Mock Splunk for triage tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from services.case_api.app import create_app as create_case_app
from services.mock_splunk.app import create_app as create_splunk_app
from services.triage.clients import CaseApiClient, SplunkClient
from services.triage.config import load_settings
from services.triage.graph import TriageDeps
from services.triage.grok import FixtureGrokClient
from services.triage.http_transport import InProcessTransport


@pytest.fixture
def triage_stack(tmp_path: Path) -> Iterator[TriageDeps]:
    case_app = create_case_app(db_path=str(tmp_path / "cases.db"))
    splunk_app = create_splunk_app()
    case_transport = InProcessTransport(case_app)
    splunk_transport = InProcessTransport(splunk_app)
    case_client = CaseApiClient("http://case.test", transport=case_transport)
    splunk_client = SplunkClient("http://splunk.test", transport=splunk_transport)
    splunk_client.reset()
    fixture = Path(__file__).resolve().parent / "fixtures" / "grok_seed_ok.json"
    grok_client = FixtureGrokClient(fixture, model_requested="grok-3-mini")
    settings = load_settings()
    deps = TriageDeps(
        case_client=case_client,
        splunk_client=splunk_client,
        grok_client=grok_client,
        routing=settings.routing_config,
        model_requested="grok-3-mini",
    )
    try:
        yield deps
    finally:
        case_client.close()
        splunk_client.close()