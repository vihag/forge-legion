"""End-to-end stream mix ratio (C7 MIX1, MIX2)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from contracts.models import CaseStatus
from services.case_api.app import create_app as create_case_app
from services.mock_splunk.app import create_app as create_splunk_app
from services.mock_splunk.stream import generate, reset_registry
from services.triage.clients import CaseApiClient, SplunkClient
from services.triage.config import load_settings
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import StreamFixtureGrokClient
from services.triage.http_transport import InProcessTransport

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@pytest.fixture
def mix_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TriageDeps, object]]:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "tests/fixtures/scenarios/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(root / "tests/fixtures/scenarios"))
    case_app = create_case_app(db_path=str(tmp_path / "cases.db"))
    splunk_app = create_splunk_app()
    case_client = CaseApiClient("http://case.test", transport=InProcessTransport(case_app))
    splunk_client = SplunkClient("http://splunk.test", transport=InProcessTransport(splunk_app))
    settings = load_settings()
    deps = TriageDeps(
        case_client=case_client,
        splunk_client=splunk_client,
        grok_client=StreamFixtureGrokClient(model_requested="grok-3-mini"),
        routing=settings.routing_config,
        model_requested="grok-3-mini",
    )
    try:
        yield deps, splunk_app
    finally:
        case_client.close()
        splunk_client.close()


def _ingest_stream(splunk_app, seed: int, count: int) -> list[object]:
    store = splunk_app.state.store
    store.reset()
    reset_registry()
    items, _ = generate(seed, count)
    for item in items:
        store._ingest_stream_item(item)
    return items


def _run_batch(deps: TriageDeps, splunk_app, seed: int, count: int) -> list[dict[str, object]]:
    items = _ingest_stream(splunk_app, seed, count)
    rows: list[dict[str, object]] = []
    for item in items:
        run_triage(deps, item.alert.alert_id)
        assert item.alert.alert_id
        listed = deps.case_client.list_cases_by_alert_id(item.alert.alert_id)
        case = deps.case_client.get_case(listed.items[0].id)
        rows.append(
            {
                "status": case.status,
                "route": case.route,
                "splunk_severity": case.splunk_severity,
                "triage_severity": case.triage_severity,
            }
        )
    return rows


@pytest.mark.parametrize("count", [100, 200])
def test_default_mix_ratio_seed42(
    mix_stack: tuple[TriageDeps, object],
    count: int,
) -> None:
    deps, splunk_app = mix_stack
    rows = _run_batch(deps, splunk_app, 42, count)
    total = len(rows)
    auto = sum(1 for row in rows if row["status"] == CaseStatus.AUTO_RESOLVED)
    collaborative = sum(
        1 for row in rows if row["status"] == CaseStatus.TRIAGED and row["route"] == "collaborative"
    )
    needs_review = sum(1 for row in rows if row["status"] == CaseStatus.NEEDS_REVIEW)
    assert 0.75 <= auto / total <= 0.85
    assert 0.15 <= collaborative / total <= 0.20
    assert needs_review / total <= 0.05
  # At least one uplift among collaborative cases.
    uplifts = [
        row
        for row in rows
        if row["status"] == CaseStatus.TRIAGED
        and row["route"] == "collaborative"
        and row["triage_severity"] is not None
        and _SEVERITY_RANK[row["triage_severity"]] > _SEVERITY_RANK[row["splunk_severity"]]
    ]
    assert len(uplifts) >= 1


@pytest.mark.parametrize("count", [100, 200])
def test_mix2_auto_resolve_disabled_stays_triaged(
    mix_stack: tuple[TriageDeps, object],
    count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "0")
    deps, splunk_app = mix_stack
    rows = _run_batch(deps, splunk_app, 42, count)
    assert not any(row["status"] == CaseStatus.AUTO_RESOLVED for row in rows)
    assert all(row["status"] == CaseStatus.TRIAGED for row in rows)
    assert not any(row["status"] == CaseStatus.NEEDS_REVIEW for row in rows)