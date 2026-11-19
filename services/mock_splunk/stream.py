"""Scenario-driven alert stream generator."""

from __future__ import annotations

import copy
import json
import os
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from contracts.mock_splunk import Alert, Entities, SplunkEvidence
from contracts.scenarios import Scenario
from services.mock_splunk.scenarios import (
    DEFAULT_SCENARIO_DIR,
    LoadedScenarios,
    load_scenarios,
    pool_for_scenario,
    render_template,
    resolve_entities,
)

Clock = Callable[[], datetime]

_REGISTRY: dict[str, dict[str, Any]] = {}
_LOADED: LoadedScenarios | None = None


@dataclass(frozen=True)
class GeneratedStreamItem:
    alert: Alert
    evidence: list[SplunkEvidence]
    scenario_id: str
    pool: str
    fixture_grok: dict[str, Any]


def _scenario_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[2]
    world = Path(os.environ.get("SCENARIO_WORLD", str(root / "config" / "world.yaml")))
    directory = Path(os.environ.get("SCENARIO_DIR", str(DEFAULT_SCENARIO_DIR)))
    return world, directory


def _loaded() -> LoadedScenarios:
    global _LOADED
    if _LOADED is None:
        world, directory = _scenario_paths()
        _LOADED = load_scenarios(world_path=world, scenario_dir=directory)
    return _LOADED


def reset_registry() -> None:
    global _LOADED
    _LOADED = None
    for key in [item for item in _REGISTRY if item.startswith("SPL-STR-")]:
        del _REGISTRY[key]


def list_scenarios() -> tuple[Scenario, ...]:
    return _loaded().scenarios


def scenario_by_id(scenario_id: str) -> Scenario | None:
    for scenario in _loaded().scenarios:
        if scenario.id == scenario_id:
            return scenario
    return None


def _severity_one_below(severity: str) -> str:
    order = ["critical", "high", "medium", "low"]
    return order[min(order.index(severity) + 1, len(order) - 1)]


def _assessed_severity(scenario: Scenario) -> str:
    if scenario.assessed_severity is not None:
        return scenario.assessed_severity
    if scenario.outcome_class == "hitl_upgrade":
        return "high"
    if scenario.outcome_class == "hitl_downgrade":
        return _severity_one_below(scenario.splunk_severity)
    return scenario.splunk_severity


def _grok_fields(scenario: Scenario) -> dict[str, Any]:
    mapping: dict[str, tuple[str, str, float, str]] = {
        "auto_benign": ("benign", "none", 0.92, "autonomous"),
        "auto_fp": ("false_positive", "none", 0.92, "autonomous"),
        "hitl_confirm": ("malicious", "escalate", 0.85, "collaborative"),
        "hitl_downgrade": ("suspicious", "escalate", 0.75, "collaborative"),
        "hitl_upgrade": ("malicious", "contain", 0.88, "collaborative"),
    }
    verdict, action, confidence, route = mapping[scenario.outcome_class]
    return {
        "original_severity": scenario.splunk_severity,
        "assessed_severity": _assessed_severity(scenario),
        "assessment_confidence": confidence,
        "rationale": f"Fixture scenario {scenario.id} ({scenario.outcome_class}).",
        "evidence_references": [],
        "mitre_techniques": list(scenario.mitre_techniques) or ["T1078"],
        "recommended_route": route,
        "additional_evidence_needed": [],
        "verdict": verdict,
        "recommended_action": action,
    }


def _build_fixture_payload(scenario: Scenario, evidence_ids: list[str]) -> dict[str, Any]:
    body = _grok_fields(scenario)
    body["evidence_references"] = evidence_ids
    return {
        "id": f"fixture-{scenario.id}",
        "model": "fixture",
        "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280},
        "choices": [{"message": {"content": json.dumps(body)}}],
    }


def fixture_grok_for_alert(
    alert_id: str,
    scenario_id: str | None,
    *,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    if alert_id in _REGISTRY:
        return copy.deepcopy(_REGISTRY[alert_id])
    if scenario_id is None:
        return None
    scenario = scenario_by_id(scenario_id)
    if scenario is None:
        return None
    # Worker processes lack the emitter-side registry; use live Splunk evidence ids.
    refs = list(evidence_ids or [])
    if not refs:
        return None
    return _build_fixture_payload(scenario, refs)


def _weighted_pick(rng: random.Random, scenarios: tuple[Scenario, ...]) -> Scenario:
    pool = [scenario for scenario in scenarios if scenario.weight > 0]
    if not pool:
        raise RuntimeError("no weighted scenarios")
    weights = [scenario.weight for scenario in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def _entities_to_splunk(entities: dict[str, dict[str, Any]]) -> Entities:
    user: str | None = None
    hosts: list[str] = []
    src_country: str | None = None
    for entity in entities.values():
        if "home_country" in entity:
            user = str(entity["id"])
            src_country = str(entity["home_country"])
        if "role" in entity:
            hosts.append(str(entity["id"]))
    return Entities(user=user, hosts=hosts, src_country=src_country)


def _emit_one(
    *,
    scenario: Scenario,
    stream_index: int,
    clock: Clock | None,
    rng: random.Random,
) -> GeneratedStreamItem:
    loaded = _loaded()
    entities = resolve_entities(loaded.world, scenario, random=rng)
    alert_id = f"SPL-STR-{stream_index + 1:06d}"
    title = render_template(scenario.alert.title, entities)
    summary = render_template(scenario.alert.description, entities)
    if clock is None:
        # Offline batch generation: synthesize spaced timestamps from a fixed epoch.
        triggered_at = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(
            seconds=stream_index * 37
        )
    else:
        # Live stream: the clock already advances per tick; adding an index offset
        # would push alerts into the future and make time-to-triage negative.
        triggered_at = clock()
    splunk_entities = _entities_to_splunk(entities)
    mitre = scenario.mitre_techniques or ["T1078"]
    alert = Alert(
        seq=stream_index + 1,
        alert_id=alert_id,
        title=title,
        splunk_severity=scenario.splunk_severity,
        summary=summary,
        rule_name=scenario.splunk_rule_name,
        mitre_techniques=mitre,
        triggered_at=triggered_at,
        emitted_at=triggered_at,
        replay_of=None,
        entities=splunk_entities,
        scenario_id=scenario.id,
    )
    evidence_rows: list[SplunkEvidence] = []
    for index, record in enumerate(scenario.evidence, start=1):
        evidence_id = f"EV-STR-{stream_index + 1:04d}-{index}"
        timestamp = triggered_at + timedelta(seconds=record.offset_s)
        rendered = {
            key: render_template(str(value), entities) if isinstance(value, str) else value
            for key, value in record.fields.items()
        }
        evidence_rows.append(
            SplunkEvidence(
                evidence_id=evidence_id,
                source=record.source,
                timestamp=timestamp,
                alert_ids=[alert_id],
                entities=splunk_entities,
                content={"sourcetype": record.sourcetype, **rendered},
            )
        )
    evidence_ids = [row.evidence_id for row in evidence_rows]
    fixture = _build_fixture_payload(scenario, evidence_ids)
    _REGISTRY[alert_id] = copy.deepcopy(fixture)
    pool = pool_for_scenario(loaded.world, scenario, entities)
    return GeneratedStreamItem(
        alert=alert,
        evidence=evidence_rows,
        scenario_id=scenario.id,
        pool=pool,
        fixture_grok=fixture,
    )


def generate(
    seed: int,
    n: int,
    start_index: int = 0,
    *,
    clock: Clock | None = None,
    scenario_dir: Path | None = None,
) -> list[GeneratedStreamItem]:
    del scenario_dir
    scenarios = _loaded().scenarios
    results: list[GeneratedStreamItem] = []
    for offset in range(n):
        stream_index = start_index + offset
        pick_rng = random.Random(f"{seed}:pick:{stream_index}")
        entity_rng = random.Random(f"{seed}:entity:{stream_index}")
        scenario = _weighted_pick(pick_rng, scenarios)
        results.append(
            _emit_one(scenario=scenario, stream_index=stream_index, clock=clock, rng=entity_rng)
        )
    return results


def script_schedule(scenarios: tuple[Scenario, ...]) -> list[Scenario]:
    return sorted(
        [scenario for scenario in scenarios if scenario.demo_slot is not None],
        key=lambda item: (item.demo_slot or 0, item.id),
    )