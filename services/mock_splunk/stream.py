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

import yaml

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
from services.mock_splunk.store import EVIDENCE_LEAD

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


def _routine_scenarios(scenarios: tuple[Scenario, ...]) -> list[Scenario]:
    return [
        scenario
        for scenario in scenarios
        if scenario.weight > 0 and scenario.outcome_class.startswith("auto_")
    ]


def _hitl_scenarios(scenarios: tuple[Scenario, ...]) -> list[Scenario]:
    return [
        scenario
        for scenario in scenarios
        if scenario.weight > 0 and not scenario.outcome_class.startswith("auto_")
    ]


def hitl_total(scenarios: tuple[Scenario, ...]) -> int:
    return len(_hitl_scenarios(scenarios))


def default_script_path(scenario_dir: Path | None = None) -> Path:
    """``demo_script.yaml`` next to the scenario dir; ``SCENARIO_SCRIPT`` overrides."""
    override = os.environ.get("SCENARIO_SCRIPT")
    if override:
        return Path(override)
    directory = scenario_dir if scenario_dir is not None else _scenario_paths()[1]
    return directory.parent / "demo_script.yaml"


def _script_file_plan(
    scenarios: tuple[Scenario, ...],
    path: Path,
) -> list[tuple[int, str, int]] | None:
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    events = raw.get("events") or []
    known = {scenario.id for scenario in scenarios}
    missing = sorted({str(event["scenario"]) for event in events} - known)
    if missing:
        raise ValueError(f"{path.name}: unknown scenarios {missing}")
    occurrences: dict[str, int] = {}
    entries: list[tuple[int, str, int]] = []
    for event in sorted(events, key=lambda item: int(item["at_s"])):
        offset_s = int(event["at_s"])
        if offset_s < 0:
            raise ValueError(f"{path.name}: at_s must be >= 0")
        scenario_id = str(event["scenario"])
        occurrence = occurrences.get(scenario_id, 0)
        occurrences[scenario_id] = occurrence + 1
        entries.append((offset_s, scenario_id, occurrence))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    return entries


def plan_script(
    scenarios: tuple[Scenario, ...],
    seed: int,
    *,
    script_path: Path | None = None,
) -> list[tuple[int, str, int]]:
    """Single script planner: ``(offset_s, scenario_id, occurrence)`` sorted by offset.

    ``demo_script.yaml`` (events ``at_s`` + ``scenario``) is the source of truth when present;
    otherwise per-scenario ``demo_offset_s`` / ``demo_slot`` (slot n -> n*60 s) are used.
    """
    del seed
    scripted = _script_file_plan(scenarios, script_path or default_script_path())
    if scripted is not None:
        return scripted
    entries: list[tuple[int, str, int]] = []
    for scenario in scenarios:
        offsets = scenario.script_offsets
        if not offsets:
            continue
        for occurrence, offset_s in enumerate(offsets):
            entries.append((offset_s, scenario.id, occurrence))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    return entries


def _pick_hitl(
    rng: random.Random,
    scenarios: tuple[Scenario, ...],
    hitl_used: frozenset[str],
) -> tuple[Scenario, frozenset[str]]:
    hitl = _hitl_scenarios(scenarios)
    if not hitl:
        raise RuntimeError("no hitl scenarios")
    available = [scenario for scenario in hitl if scenario.id not in hitl_used]
    next_used = set(hitl_used)
    if not available:
        available = hitl
        next_used = set()
    chosen = _weighted_pick(rng, tuple(available))
    next_used.add(chosen.id)
    if len(next_used) >= len(hitl):
        next_used = set()
    return chosen, frozenset(next_used)


def _pick_scenario(
    rng: random.Random,
    scenarios: tuple[Scenario, ...],
    hitl_used: frozenset[str],
) -> tuple[Scenario, frozenset[str]]:
    routine = _routine_scenarios(scenarios)
    hitl = _hitl_scenarios(scenarios)
    routine_weight = sum(scenario.weight for scenario in routine)
    hitl_weight = sum(scenario.weight for scenario in hitl)
    total = routine_weight + hitl_weight
    if total <= 0:
        raise RuntimeError("no weighted scenarios")
    if rng.random() * total < routine_weight:
        return _weighted_pick(rng, tuple(routine)), hitl_used
    return _pick_hitl(rng, scenarios, hitl_used)


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


_ENTITY_OVERRIDE_KEYS = ("src_ip", "src_country")


def _with_field_overrides(base: Entities, fields: dict[str, Any]) -> Entities:
    """Let a record's own ``src_ip`` / ``src_country`` win over the user's home defaults.

    Without this every streamed alert and record was tagged with the user's home
    country, so an alert that says "from RO" carried ``src_country=SG`` and an
    entity search for the country it names found nothing.
    """
    updates = {
        key: str(fields[key])
        for key in _ENTITY_OVERRIDE_KEYS
        if isinstance(fields.get(key), str | int | float) and str(fields[key])
    }
    return base.model_copy(update=updates) if updates else base


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
    alert_fields = {
        key: render_template(value, entities) if isinstance(value, str) else value
        for key, value in scenario.alert.fields.items()
    }
    alert_entities = Entities.model_validate(
        _with_field_overrides(splunk_entities, alert_fields).model_dump()
    )
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
        entities=alert_entities,
        scenario_id=scenario.id,
    )
    evidence_rows: list[SplunkEvidence] = []
    # Evidence must never post-date the alert (an analyst querying "now" would see
    # future events). Shift the whole story back so the latest record lands at the
    # alert time (minus ``EVIDENCE_LEAD``, so a search ending at the alert time still
    # returns it); relative order and gaps between records are preserved.
    latest_offset_s = max([0, *(record.offset_s for record in scenario.evidence)])
    for index, record in enumerate(scenario.evidence, start=1):
        evidence_id = f"EV-STR-{stream_index + 1:04d}-{index}"
        timestamp = (
            triggered_at
            + timedelta(seconds=record.offset_s - latest_offset_s)
            - EVIDENCE_LEAD
        )
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
                entities=Entities.model_validate(
                    _with_field_overrides(splunk_entities, rendered).model_dump()
                ),
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
    hitl_used: frozenset[str] | None = None,
) -> tuple[list[GeneratedStreamItem], frozenset[str]]:
    del scenario_dir
    scenarios = _loaded().scenarios
    used = frozenset() if hitl_used is None else hitl_used
    results: list[GeneratedStreamItem] = []
    for offset in range(n):
        stream_index = start_index + offset
        pick_rng = random.Random(f"{seed}:pick:{stream_index}")
        entity_rng = random.Random(f"{seed}:entity:{stream_index}")
        scenario, used = _pick_scenario(pick_rng, scenarios, used)
        results.append(
            _emit_one(scenario=scenario, stream_index=stream_index, clock=clock, rng=entity_rng)
        )
    return results, used


def script_schedule(scenarios: tuple[Scenario, ...]) -> list[Scenario]:
    """Scenarios in script firing order (repeats kept), derived from ``plan_script``."""
    by_id = {scenario.id: scenario for scenario in scenarios}
    return [by_id[scenario_id] for _offset, scenario_id, _occ in plan_script(scenarios, seed=0)]
