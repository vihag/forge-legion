"""Hero alert is re-stamped on reset (C8 steer)."""

from datetime import UTC, datetime
from pathlib import Path

from httpx import Client

from contracts.mock_splunk import format_utc
from services.mock_splunk.app import create_app
from services.mock_splunk.store import _load_fixtures, default_fixtures_dir
from services.triage.http_transport import InProcessTransport


def test_hero_alert_restamp_on_reset() -> None:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    class _Clock:
        def __call__(self) -> datetime:
            return now

    loaded = _load_fixtures(default_fixtures_dir())
    hero_orig = next(a for a in loaded.alerts if a.alert_id == "SPL-2026-0001")
    hero_rows = [row for row in loaded.evidence if "SPL-2026-0001" in row.alert_ids]
    latest = max([hero_orig.triggered_at, *(row.timestamp for row in hero_rows)])
    # Fixture story has records after the alert; after reset they must not be in the future.
    assert latest > hero_orig.triggered_at
    offsets = {
        row.evidence_id: (row.timestamp - latest).total_seconds() for row in hero_rows
    }
    app = create_app(clock=_Clock())
    with Client(transport=InProcessTransport(app), base_url="http://test") as client:
        client.post("/v1/reset", json={})
        alert = client.get("/v1/alerts/SPL-2026-0001").json()
        assert alert["triggered_at"] == format_utc(now)
        triggered = datetime.fromisoformat(alert["triggered_at"].replace("Z", "+00:00"))
        evidence = client.get("/v1/alerts/SPL-2026-0001/evidence").json()["items"]
        for row in evidence:
            ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            assert (ts - triggered).total_seconds() == offsets[row["evidence_id"]]
            assert ts <= now
        assert max(
            datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) for row in evidence
        ) == now


def test_hero_time_to_triage_small_after_reset(tmp_path) -> None:
    from datetime import UTC, datetime

    from services.case_api.app import create_app as create_case_app
    from services.triage.clients import CaseApiClient, SplunkClient
    from services.triage.config import load_settings
    from services.triage.graph import TriageDeps, run_triage
    from services.triage.grok import FixtureGrokClient

    fixture = Path(__file__).resolve().parents[1] / "triage" / "fixtures" / "grok_seed_ok.json"
    now = datetime.now(UTC)
    splunk = create_app(clock=lambda: now)
    case_app = create_case_app(db_path=str(tmp_path / "hero.db"))
    transport_s = InProcessTransport(splunk)
    transport_c = InProcessTransport(case_app)
    with Client(transport=transport_s, base_url="http://splunk") as splunk_http:
        splunk_http.post("/v1/reset", json={})
    settings = load_settings()
    deps = TriageDeps(
        case_client=CaseApiClient("http://case", transport=transport_c),
        splunk_client=SplunkClient("http://splunk", transport=transport_s),
        grok_client=FixtureGrokClient(fixture, model_requested="grok-3-mini"),
        routing=settings.routing_config,
        model_requested="grok-3-mini",
    )
    result = run_triage(deps, "SPL-2026-0001")
    assert result.outcome == "triaged"
    metrics = Client(transport=transport_c, base_url="http://case").get("/v1/metrics").json()
    timing = metrics["time_to_triage_s"]
    assert timing["n"] >= 1
    assert timing["median"] is not None
    assert timing["median"] < 600

def test_streamed_evidence_never_after_alert(monkeypatch) -> None:
    """Every scenario's evidence is at or before its alert; gaps between records kept."""
    from services.mock_splunk import stream

    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "config/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(root / "config/scenarios"))
    stream.reset_registry()
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    try:
        scenarios = stream.list_scenarios()
        assert any(max(r.offset_s for r in s.evidence) > 0 for s in scenarios if s.evidence)
        for index, scenario in enumerate(scenarios):
            item = stream._emit_one(
                scenario=scenario,
                stream_index=index,
                clock=lambda: now,
                rng=__import__("random").Random(index),
            )
            assert item.alert.triggered_at == now
            stamps = [row.timestamp for row in item.evidence]
            assert all(ts <= now for ts in stamps), scenario.id
            records = scenario.evidence
            gaps = [b.offset_s - a.offset_s for a, b in zip(records, records[1:], strict=False)]
            got = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)]
            assert got == gaps, scenario.id
    finally:
        stream.reset_registry()
