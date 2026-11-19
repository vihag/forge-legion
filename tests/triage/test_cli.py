"""Triage CLI fixture vs http Grok client selection."""

import json
from pathlib import Path

import pytest

from services.case_api.app import create_app as create_case_app
from services.mock_splunk.app import create_app as create_splunk_app
from services.triage import __main__ as triage_cli
from services.triage.clients import CaseApiClient, SplunkClient
from services.triage.http_transport import InProcessTransport

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "grok_seed_ok.json"
ALERT_ID = "SPL-2026-0001"


@pytest.fixture
def triage_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_app = create_case_app(db_path=str(tmp_path / "cases.db"))
    splunk_app = create_splunk_app()
    case_transport = InProcessTransport(case_app)
    splunk_transport = InProcessTransport(splunk_app)
    monkeypatch.setenv("CASE_API_URL", "http://case.test")
    monkeypatch.setenv("MOCK_SPLUNK_URL", "http://splunk.test")
    monkeypatch.setenv("GROK_FIXTURE_PATH", str(FIXTURE))

    def case_client_factory(url: str, **kwargs: object) -> CaseApiClient:
        del url, kwargs
        return CaseApiClient("http://case.test", transport=case_transport)

    def splunk_client_factory(url: str, **kwargs: object) -> SplunkClient:
        del url, kwargs
        client = SplunkClient("http://splunk.test", transport=splunk_transport)
        client.reset()
        return client

    monkeypatch.setattr(triage_cli, "CaseApiClient", case_client_factory)
    monkeypatch.setattr(triage_cli, "SplunkClient", splunk_client_factory)


def test_fixture_mode_runs_without_api_key(
    triage_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("GROK_CLIENT", "fixture")
    code = triage_cli.main(["run", "--alert-id", ALERT_ID])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["outcome"] == "triaged"
    assert "grok_client=fixture" in captured.err


def test_fixture_mode_missing_path_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_CLIENT", "fixture")
    monkeypatch.delenv("GROK_FIXTURE_PATH", raising=False)
    code = triage_cli.main(["run", "--alert-id", ALERT_ID])
    assert code == 2


def test_http_mode_still_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_CLIENT", "http")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    code = triage_cli.main(["run", "--alert-id", ALERT_ID])
    assert code == 2