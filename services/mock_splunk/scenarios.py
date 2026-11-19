"""Load and validate scenario YAML plus ``check`` CLI."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from contracts.models import Severity
from contracts.scenarios import (
    EntityBinding,
    EntityPick,
    Scenario,
    ScenarioFile,
    WorldFile,
)

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TEMPLATE_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\}")

_SEVERITY_RANK: dict[Severity, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / "config" / "world.yaml"
DEFAULT_SCENARIO_DIR = ROOT / "config" / "scenarios"


@dataclass(frozen=True)
class LoadedScenarios:
    world: WorldFile
    scenarios: tuple[Scenario, ...]
    files: tuple[ScenarioFile, ...]


def load_world(path: Path) -> WorldFile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return WorldFile.model_validate(raw)


def load_scenario_file(path: Path) -> ScenarioFile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    document = ScenarioFile.model_validate(raw)
    if document.id != path.stem:
        raise ValueError(f"{path.name}: id {document.id!r} must match filename stem")
    return document


def _script_offsets(document: ScenarioFile) -> tuple[int, ...] | None:
    if document.demo_offset_s is not None:
        if isinstance(document.demo_offset_s, int):
            if document.demo_offset_s < 0:
                raise ValueError("demo_offset_s must be >= 0")
            return (document.demo_offset_s,)
        offsets = tuple(int(value) for value in document.demo_offset_s)
        if any(value < 0 for value in offsets):
            raise ValueError("demo_offset_s entries must be >= 0")
        return offsets
    if document.demo_slot is not None:
        return (document.demo_slot * 60,)
    return None


def scenario_from_file(document: ScenarioFile) -> Scenario:
    return Scenario(
        id=document.id,
        title=document.title,
        splunk_rule_name=document.splunk_rule_name,
        splunk_severity=document.splunk_severity,
        outcome_class=document.outcome_class,
        weight=document.weight,
        demo_slot=document.demo_slot,
        script_offsets=_script_offsets(document),
        assessed_severity=document.assessed_severity,
        mitre_techniques=list(document.mitre_techniques),
        entities=document.entities,
        alert=document.alert,
        evidence=list(document.evidence),
    )


def load_scenarios(
    *,
    world_path: Path | None = None,
    scenario_dir: Path | None = None,
) -> LoadedScenarios:
    world = load_world(world_path or DEFAULT_WORLD)
    directory = scenario_dir or DEFAULT_SCENARIO_DIR
    paths = sorted(path for path in directory.glob("*.yaml") if path.name != "world.yaml")
    files: list[ScenarioFile] = []
    scenarios: list[Scenario] = []
    for path in paths:
        document = load_scenario_file(path)
        files.append(document)
        scenarios.append(scenario_from_file(document))
    return LoadedScenarios(world=world, scenarios=tuple(scenarios), files=tuple(files))


def check(world_path: Path, scenario_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        world = load_world(world_path)
    except (ValidationError, ValueError, yaml.YAMLError) as exc:
        return [f"world: {exc}"]
    try:
        _validate_pools(world)
    except ValueError as exc:
        errors.append(f"world: V4/V5 {exc}")
    for path in sorted(path for path in scenario_dir.glob("*.yaml") if path.name != "world.yaml"):
        errors.extend(_check_file(world, path))
    return errors


def _check_file(world: WorldFile, path: Path) -> list[str]:
    prefix = path.name
    errors: list[str] = []
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(raw_text)
        document = ScenarioFile.model_validate(raw)
    except (ValidationError, ValueError, yaml.YAMLError) as exc:
        return [f"{prefix}: V1 {exc}"]
    if document.id != path.stem:
        errors.append(f"{prefix}: V1 id must match filename stem")
    if _ISO_DATE_RE.search(raw_text):
        errors.append(f"{prefix}: V6 absolute date found")
    try:
        _validate_refs(world, document)
    except ValueError as exc:
        errors.append(f"{prefix}: V2 {exc}")
    try:
        _validate_auto_pool(world, document)
    except ValueError as exc:
        errors.append(f"{prefix}: V3 {exc}")
    try:
        _validate_outcome_severity(document)
    except ValueError as exc:
        errors.append(f"{prefix}: V7 {exc}")
    if document.demo_slot is not None and document.demo_offset_s is not None:
        errors.append(f"{prefix}: V8 demo_slot and demo_offset_s are mutually exclusive")
    return errors


def _validate_refs(world: WorldFile, document: ScenarioFile) -> None:
    for name, binding in document.entities.items():
        if binding.ref is not None:
            kind, entity_id = binding.ref.split(":", 1)
            _must_resolve(world, kind, entity_id)
        elif binding.pick is not None:
            matches = _pick_matches(world, binding.pick)
            if not matches:
                raise ValueError(f"pick for entity {name!r} matches nothing")


def _validate_auto_pool(world: WorldFile, document: ScenarioFile) -> None:
    if not document.outcome_class.startswith("auto_"):
        return
    if document.splunk_severity not in ("low", "medium"):
        raise ValueError("auto outcome requires splunk_severity low|medium")
    for name, binding in document.entities.items():
        entity = _resolve_binding(world, binding, random=None)
        if entity is None:
            raise ValueError(f"entity {name!r} unresolved")
        pool = entity.get("pool")
        if pool != "routine":
            raise ValueError(f"auto outcome entity {name!r} must be routine pool")


def _validate_pools(world: WorldFile) -> None:
    routine_users = [user for user in world.users if user.pool == "routine"]
    hitl_users = [user for user in world.users if user.pool == "hitl"]
    routine_hosts = [host for host in world.hosts if host.pool == "routine"]
    hitl_hosts = [host for host in world.hosts if host.pool == "hitl"]
    if len({user.id for user in world.users}) != len(world.users):
        raise ValueError("duplicate user id")
    if len({host.id for host in world.hosts}) != len(world.hosts):
        raise ValueError("duplicate host id")
    if set(user.id for user in routine_users) & {user.id for user in hitl_users}:
        raise ValueError("user pools overlap")
    if {host.id for host in routine_hosts} & {host.id for host in hitl_hosts}:
        raise ValueError("host pools overlap")
    for user in routine_users:
        if user.privileged:
            raise ValueError(f"routine user {user.id} is privileged")
    for host in routine_hosts:
        if host.crown_jewel:
            raise ValueError(f"routine host {host.id} is crown jewel")
    if len(routine_users) < 60:
        raise ValueError("routine pool needs >= 60 users")
    if len(routine_hosts) < 30:
        raise ValueError("routine pool needs >= 30 hosts")


def _validate_outcome_severity(document: ScenarioFile) -> None:
    splunk = document.splunk_severity
    assessed = _assessed_for_scenario(document)
    if document.outcome_class == "hitl_upgrade" and (
        _SEVERITY_RANK[assessed] <= _SEVERITY_RANK[splunk]
    ):
        raise ValueError("hitl_upgrade needs assessed > splunk")
    if document.outcome_class == "hitl_downgrade" and splunk not in ("high", "critical"):
        raise ValueError("hitl_downgrade needs splunk high|critical")
    if document.outcome_class == "hitl_downgrade" and (
        _SEVERITY_RANK[assessed] >= _SEVERITY_RANK[splunk]
    ):
        raise ValueError("hitl_downgrade needs assessed below splunk")


def _assessed_for_scenario(document: ScenarioFile) -> Severity:
    if document.assessed_severity is not None:
        return document.assessed_severity
    if document.outcome_class == "hitl_upgrade":
        return "high"
    return document.splunk_severity


def _must_resolve(world: WorldFile, kind: str, entity_id: str) -> Mapping[str, Any]:
    entity = _lookup_entity(world, kind, entity_id)
    if entity is None:
        raise ValueError(f"unknown ref {kind}:{entity_id}")
    return entity


def _lookup_entity(world: WorldFile, kind: str, entity_id: str) -> dict[str, Any] | None:
    if kind == "user":
        for user in world.users:
            if user.id == entity_id:
                return user.model_dump()
    if kind == "host":
        for host in world.hosts:
            if host.id == entity_id:
                return host.model_dump()
    if kind == "domain":
        for domain in world.domains:
            if domain.domain == entity_id:
                return domain.model_dump()
    return None


def _pick_matches(world: WorldFile, pick: EntityPick) -> list[dict[str, Any]]:
    rows: Sequence[Any]
    if pick.kind == "user":
        rows = world.users
    elif pick.kind == "host":
        rows = world.hosts
    else:
        rows = world.domains
    matched: list[dict[str, Any]] = []
    for row in rows:
        data = row.model_dump()
        if all(data.get(key) == value for key, value in pick.where.items()):
            matched.append(data)
    return matched


def _resolve_binding(
    world: WorldFile,
    binding: EntityBinding,
    *,
    random: Any,
) -> dict[str, Any] | None:
    if binding.ref is not None:
        kind, entity_id = binding.ref.split(":", 1)
        return dict(_must_resolve(world, kind, entity_id))
    if binding.pick is not None:
        matches = _pick_matches(world, binding.pick)
        if not matches:
            return None
        if random is None:
            return dict(matches[0])
        return dict(random.choice(matches))
    return None


def resolve_entities(
    world: WorldFile,
    scenario: Scenario,
    *,
    random: Any,
) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}
    for name, binding in scenario.entities.items():
        entity = _resolve_binding(world, binding, random=random)
        if entity is None:
            raise ValueError(f"failed to resolve entity {name!r}")
        resolved[name] = entity
    return resolved


def render_template(value: str, entities: Mapping[str, Mapping[str, Any]]) -> str:
    def repl(match: re.Match[str]) -> str:
        entity_name, attr = match.group(1), match.group(2)
        entity = entities.get(entity_name)
        if entity is None:
            raise ValueError(f"unknown template entity {entity_name!r}")
        if attr not in entity:
            raise ValueError(f"unknown template attr {entity_name}.{attr}")
        return str(entity[attr])

    return _TEMPLATE_RE.sub(repl, value)


def render_mapping(
    data: Mapping[str, Any],
    entities: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = render_template(value, entities)
        elif isinstance(value, list):
            result[key] = [
                render_template(item, entities) if isinstance(item, str) else item for item in value
            ]
        else:
            result[key] = value
    return result


def pool_for_scenario(
    world: WorldFile,
    scenario: Scenario,
    entities: Mapping[str, Mapping[str, Any]],
) -> str:
    del world, entities
    if scenario.outcome_class.startswith("auto_"):
        return "routine"
    return "hitl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="services.mock_splunk.scenarios")
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help="Validate world and scenarios")
    check_cmd.add_argument("--world", type=Path, required=True)
    check_cmd.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        errors = check(args.world, args.dir)
        if errors:
            for line in errors:
                print(line, file=sys.stderr)
            return 1
        loaded = load_scenarios(world_path=args.world, scenario_dir=args.dir)
        from services.mock_splunk.stream import plan_script

        plan = plan_script(loaded.scenarios, seed=0)
        print(f"script plan: {len(plan)} events")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())