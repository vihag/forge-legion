"""Pure routing policy loaded from config/routing.yaml."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts.models import Severity
from contracts.triage import GrokAssessment, Route

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROUTING_PATH = ROOT / "config" / "routing.yaml"

SeverityList = list[Severity]


class RuleWhen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privileged_account: bool | None = None
    assessed_severity_in: SeverityList | None = None
    splunk_severity_in: SeverityList | None = None
    confidence_below: float | None = None


class RoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    when: RuleWhen
    route: Route


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    privileged_account_patterns: list[str] = Field(min_length=1)
    rules: list[RoutingRule] = Field(default_factory=list)
    default_route: Route


def load_routing(path: Path | None = None) -> RoutingConfig:
    target = DEFAULT_ROUTING_PATH if path is None else path
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("routing config must be a mapping")
    try:
        return RoutingConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def decide(
    facts: dict[str, Any],
    assessment: GrokAssessment,
    splunk_severity: Severity,
    config: RoutingConfig,
) -> tuple[Route, str]:
    """Return the policy route and the rule name that matched."""
    context = _context(facts, assessment, splunk_severity)
    for rule in config.rules:
        if _matches(rule.when, context):
            return rule.route, rule.name
    return config.default_route, "default_route"


def _context(
    facts: dict[str, Any],
    assessment: GrokAssessment,
    splunk_severity: Severity,
) -> dict[str, Any]:
    return {
        "privileged_account": bool(facts.get("privileged_account")),
        "assessed_severity": assessment.assessed_severity,
        "splunk_severity": splunk_severity,
        "assessment_confidence": assessment.assessment_confidence,
    }


def _matches(when: RuleWhen, context: dict[str, Any]) -> bool:
    checks: list[bool] = []
    if when.privileged_account is not None:
        checks.append(context["privileged_account"] is when.privileged_account)
    if when.assessed_severity_in is not None:
        checks.append(context["assessed_severity"] in when.assessed_severity_in)
    if when.splunk_severity_in is not None:
        checks.append(context["splunk_severity"] in when.splunk_severity_in)
    if when.confidence_below is not None:
        checks.append(context["assessment_confidence"] < when.confidence_below)
    return len(checks) > 0 and all(checks)


def validate_when_keys(raw: dict[str, Any]) -> None:
    """Fail fast when a rule uses an unknown when key."""
    allowed = set(RuleWhen.model_fields)
    for key in raw:
        if key not in allowed:
            raise ValueError(f"unknown when key: {key}")