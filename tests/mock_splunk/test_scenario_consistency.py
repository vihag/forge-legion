"""Alert text and fields must be backed by the scenario's own evidence.

Guards against the class of bug where an alert says "from RO via VPN-GW-01" but no
evidence record carries RO or VPN-GW-01, so an analyst's Splunk search finds nothing.
Checked for every scenario (scripted and random stream):

- every world host, world user and IPv4 address named in the alert title/summary
  appears in at least one evidence record (content or entities);
- every country code the text names as a location ("from XX", "-> XX",
  "baseline is XX") appears as a country on at least one evidence record;
- an alert ``fields.src_country`` / ``fields.src_ip`` is what the alert carries as its
  entities, and is backed by at least one evidence record.
"""

from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from services.mock_splunk import stream

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 12, 3, 9, 30, 0, tzinfo=UTC)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]")
_COUNTRY_CONTEXT = re.compile(r"(?:\bfrom|->|baseline is)\s+([A-Z]{2})(?![\w-])")
_COUNTRY_KEYS = ("src_country", "geo_country", "country", "prior_src_country")


@pytest.fixture(scope="module")
def emitted() -> list[tuple[str, stream.GeneratedStreamItem]]:
    import os

    old = {key: os.environ.get(key) for key in ("SCENARIO_WORLD", "SCENARIO_DIR")}
    os.environ["SCENARIO_WORLD"] = str(ROOT / "config/world.yaml")
    os.environ["SCENARIO_DIR"] = str(ROOT / "config/scenarios")
    stream.reset_registry()
    try:
        items = []
        for index, scenario in enumerate(stream.list_scenarios()):
            for occurrence in range(3):  # pick-based entities vary per firing
                item = stream._emit_one(
                    scenario=scenario,
                    stream_index=index,
                    clock=lambda: NOW,
                    rng=random.Random(f"42:entity:{scenario.id}:{occurrence}"),
                )
                items.append((scenario.id, item))
        return items
    finally:
        stream.reset_registry()
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _world_ids() -> tuple[set[str], set[str]]:
    world = yaml.safe_load((ROOT / "config/world.yaml").read_text(encoding="utf-8"))
    return {h["id"] for h in world.get("hosts", [])}, {u["id"] for u in world.get("users", [])}


def _record_values(item: stream.GeneratedStreamItem) -> tuple[str, set[str]]:
    blobs = []
    countries: set[str] = set()
    for row in item.evidence:
        payload = {"content": row.content, "entities": row.entities.model_dump()}
        blobs.append(json.dumps(payload))
        if row.entities.src_country:
            countries.add(row.entities.src_country)
        for key in _COUNTRY_KEYS:
            value = row.content.get(key)
            if isinstance(value, str):
                countries.add(value)
    return "\n".join(blobs), countries


def test_scripted_scenarios_are_covered(emitted) -> None:
    script = yaml.safe_load((ROOT / "config/demo_script.yaml").read_text(encoding="utf-8"))
    ids = {scenario_id for scenario_id, _ in emitted}
    assert {event["scenario"] for event in script["events"]} <= ids


def test_alert_names_are_backed_by_evidence(emitted) -> None:
    hosts, users = _world_ids()
    problems: list[str] = []
    for scenario_id, item in emitted:
        if not item.evidence:
            continue
        text = f"{item.alert.title} {item.alert.summary}"
        blob, countries = _record_values(item)
        for token in set(_TOKEN.findall(text)):
            if (token in hosts or token in users or _IPV4.fullmatch(token)) and token not in blob:
                problems.append(f"{scenario_id}: {token!r} named in alert, not in any evidence")
        for code in set(_COUNTRY_CONTEXT.findall(text)):
            if code not in countries:
                problems.append(f"{scenario_id}: country {code} named in alert, not in evidence")
    assert not problems, "\n".join(sorted(set(problems)))


def test_alert_field_geo_matches_entities_and_evidence(emitted) -> None:
    problems: list[str] = []
    for scenario_id, item in emitted:
        scenario = stream.scenario_by_id(scenario_id)
        assert scenario is not None
        blob, countries = _record_values(item)
        country = scenario.alert.fields.get("src_country")
        if isinstance(country, str):
            if item.alert.entities.src_country != country:
                problems.append(f"{scenario_id}: alert entity country != fields {country}")
            if item.evidence and country not in countries:
                problems.append(f"{scenario_id}: fields country {country} not in evidence")
        ip = scenario.alert.fields.get("src_ip")
        if isinstance(ip, str) and "${" not in ip:
            if item.alert.entities.src_ip != ip:
                problems.append(f"{scenario_id}: alert entity src_ip != fields {ip}")
            if item.evidence and ip not in blob:
                problems.append(f"{scenario_id}: fields src_ip {ip} not in evidence")
    assert not problems, "\n".join(sorted(set(problems)))


def test_hero_story_matches_its_alert(emitted) -> None:
    item = next(item for scenario_id, item in emitted if scenario_id == "spl-2026-0001-hero")
    auth = [row for row in item.evidence if row.source == "auth"]
    ro = [row for row in auth if row.content.get("src_country") == "RO"]
    assert len(ro) == 2
    assert all(row.content["target"] == "VPN-GW-01" for row in ro)
    assert all(row.entities.src_country == "RO" for row in ro)
    assert any(row.content.get("src_country") == "SG" for row in auth)  # baseline
    assert any(row.content.get("bytes_out") == 48234496 for row in item.evidence)
