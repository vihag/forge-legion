"""Regression: typical evidence searches return the whole hero story after a reset.

After #38 the hero timeline ends at the alert, so the RO sign-ins sit ~16-22 min
before it, the SG baseline ~25 h before it, and the latest record used to sit exactly
on the alert time (excluded by the end-exclusive window). These tests pin that a
search ending at the alert time, an entity search, and the MCP tool's default window
all return every hero record, for the seeded hero and for streamed scenarios.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.mcp_adapter.config import AdapterConfig
from services.mcp_adapter.server import build_mcp_server, default_search_window
from services.mcp_adapter.splunk_client import SplunkClient
from services.mock_splunk.app import create_app
from services.mock_splunk.store import SplunkStore
from tests.mcp_adapter.asgi_transport import FastApiTestTransport
from tests.mcp_adapter.test_analyst import _session_runner

HERO = "SPL-2026-0001"
HERO_LINKED = {
    "EV-AUTH-0002",
    "EV-AUTH-0003",
    "EV-EDR-0001",
    "EV-EDR-0002",
    "EV-EDR-0003",
    "EV-PROXY-0001",
    "EV-PROXY-0002",
}
BASELINE = "EV-AUTH-0001"
# A day well after the fixture date, like a demo day.
DEMO_DAY = datetime(2026, 12, 3, 9, 30, 0, tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ids(body: dict[str, object]) -> set[str]:
    return {str(item["evidence_id"]) for item in body["items"]}  # type: ignore[index, union-attr]


@pytest.fixture
def hero_client() -> TestClient:
    return TestClient(create_app(clock=lambda: DEMO_DAY))


def _triggered(client: TestClient) -> datetime:
    alert = client.get(f"/v1/alerts/{HERO}").json()
    return datetime.fromisoformat(alert["triggered_at"].replace("Z", "+00:00"))


def test_window_ending_at_alert_time_returns_all_hero_records(hero_client: TestClient) -> None:
    triggered = _triggered(hero_client)
    assert triggered == DEMO_DAY
    linked = _ids(hero_client.get(f"/v1/alerts/{HERO}/evidence").json())
    assert linked == HERO_LINKED
    body = hero_client.get(
        "/v1/evidence",
        params={
            "source": "*",
            "start": _iso(triggered - timedelta(hours=48)),
            "end": _iso(triggered),
        },
    ).json()
    assert HERO_LINKED | {BASELINE} <= _ids(body)
    # No record is later than the alert, and the latest one is inside an end=alert window.
    stamps = [datetime.fromisoformat(i["timestamp"].replace("Z", "+00:00")) for i in body["items"]]
    assert max(stamps) < triggered


def test_auth_searches_find_ro_sign_ins_and_baseline(hero_client: TestClient) -> None:
    triggered = _triggered(hero_client)
    window = {"start": _iso(triggered - timedelta(hours=48)), "end": _iso(triggered)}
    by_user = hero_client.get(
        "/v1/evidence", params={"source": "auth", "entity": "adm.mtan", **window}
    ).json()
    assert _ids(by_user) == {BASELINE, "EV-AUTH-0002", "EV-AUTH-0003"}
    by_country = hero_client.get(
        "/v1/evidence", params={"source": "auth", "entity": "RO", **window}
    ).json()
    assert _ids(by_country) == {"EV-AUTH-0002", "EV-AUTH-0003"}
    by_gateway = hero_client.get(
        "/v1/evidence", params={"source": "auth", "entity": "VPN-GW-01", **window}
    ).json()
    assert _ids(by_gateway) == {"EV-AUTH-0002", "EV-AUTH-0003"}


def test_baseline_moves_with_the_hero_story() -> None:
    store = SplunkStore(clock=lambda: DEMO_DAY)
    rows = {row.evidence_id: row.timestamp for row in store.loaded_evidence()}
    gap = rows["EV-AUTH-0003"] - rows[BASELINE]
    # Fixture gap (2026-11-17T01:12:05Z -> 2026-11-18T02:03:41Z) is preserved.
    assert gap == timedelta(hours=24, minutes=51, seconds=36)
    assert DEMO_DAY - rows[BASELINE] < timedelta(hours=48)


def test_default_window_fills_missing_bounds() -> None:
    now = datetime(2026, 12, 3, 9, 30, 0, tzinfo=UTC)
    start, end = default_search_window(None, None, now=now)
    assert end == "2026-12-03T09:31:00Z"
    assert start == "2026-11-30T09:31:00Z"
    assert default_search_window("a", "b", now=now) == ("a", "b")
    start, end = default_search_window(None, "2026-12-03T09:00:00Z", now=now)
    assert (start, end) == ("2026-11-30T09:00:00Z", "2026-12-03T09:00:00Z")


def test_mcp_search_without_window_returns_all_hero_evidence() -> None:
    splunk = SplunkClient("http://splunk.test", transport=FastApiTestTransport(create_app()))
    runner = _session_runner(build_mcp_server(AdapterConfig(), splunk_client=splunk))
    result = runner.call_tool("search_evidence", {"source": "*"})  # type: ignore[attr-defined]
    assert not result.isError
    assert HERO_LINKED | {BASELINE} <= _ids(json.loads(result.content[0].text))
    auth = runner.call_tool(  # type: ignore[attr-defined]
        "search_evidence", {"source": "auth", "entity": "adm.mtan"}
    )
    assert _ids(json.loads(auth.content[0].text)) == {BASELINE, "EV-AUTH-0002", "EV-AUTH-0003"}


def test_streamed_scenarios_found_by_window_ending_at_alert(monkeypatch) -> None:
    from services.mock_splunk import stream

    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "config/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(root / "config/scenarios"))
    stream.reset_registry()
    try:
        for index, scenario in enumerate(stream.list_scenarios()):
            if not scenario.evidence:
                continue
            store = SplunkStore(clock=lambda: DEMO_DAY)
            store.reset(seed_hero=False)
            item = stream._emit_one(
                scenario=scenario,
                stream_index=index,
                clock=lambda: DEMO_DAY,
                rng=random.Random(index),
            )
            store._ingest_stream_item(item)
            start = min(row.timestamp for row in item.evidence) - timedelta(seconds=1)
            found = store.evidence_by_window(
                source="*", start=_iso(start), end=_iso(item.alert.triggered_at), entity=None
            )
            assert {row.evidence_id for row in found.items} == {
                row.evidence_id for row in item.evidence
            }, scenario.id
    finally:
        stream.reset_registry()


def test_streamed_hero_is_findable_by_country_and_gateway(monkeypatch) -> None:
    from services.mock_splunk import stream

    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCENARIO_WORLD", str(root / "config/world.yaml"))
    monkeypatch.setenv("SCENARIO_DIR", str(root / "config/scenarios"))
    stream.reset_registry()
    try:
        scenario = stream.scenario_by_id("spl-2026-0001-hero")
        assert scenario is not None
        store = SplunkStore(clock=lambda: DEMO_DAY)
        store.reset(seed_hero=False)
        item = stream._emit_one(
            scenario=scenario, stream_index=2, clock=lambda: DEMO_DAY, rng=random.Random(0)
        )
        store._ingest_stream_item(item)
        assert item.alert.entities.src_country == "RO"
        assert item.alert.entities.src_ip == "203.0.113.45"
        window = {
            "start": _iso(DEMO_DAY - timedelta(hours=48)),
            "end": _iso(item.alert.triggered_at),
        }
        ro = store.evidence_by_window(source="auth", entity="RO", **window)
        assert len(ro.items) == 2
        assert all(row.content["target"] == "VPN-GW-01" for row in ro.items)
        sg = store.evidence_by_window(source="auth", entity="SG", **window)
        assert [row.content["src_ip"] for row in sg.items] == ["198.51.100.23"]
        gw = store.evidence_by_window(source="*", entity="VPN-GW-01", **window)
        assert len(gw.items) == len(item.evidence)
    finally:
        stream.reset_registry()
