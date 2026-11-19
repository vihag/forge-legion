"""Routing policy is independent of model recommended_route."""

from pathlib import Path

import pytest
import yaml

from contracts.triage import GrokAssessment
from services.triage.routing import RoutingConfig, RoutingRule, RuleWhen, decide, load_routing


def _assessment(**overrides: object) -> GrokAssessment:
    base = {
        "original_severity": "medium",
        "assessed_severity": "high",
        "assessment_confidence": 0.7,
        "rationale": "test",
        "evidence_references": ["EV-AUTH-0001"],
        "mitre_techniques": ["T1078"],
        "recommended_route": "autonomous",
        "additional_evidence_needed": [],
    }
    base.update(overrides)
    return GrokAssessment.model_validate(base)


def test_privileged_account_always_collaborative(tmp_path: Path) -> None:
    config = load_routing()
    facts = {"privileged_account": True, "first_seen_country": False}
    assessment = _assessment(assessed_severity="low", recommended_route="autonomous")
    route, rule = decide(facts, assessment, "medium", config)
    assert route == "collaborative"
    assert rule == "privileged_account_collaborative"


def test_high_critical_collaborative() -> None:
    config = load_routing()
    facts = {"privileged_account": False}
    for severity in ("high", "critical"):
        assessment = _assessment(assessed_severity=severity)
        route, rule = decide(facts, assessment, "medium", config)
        assert route == "collaborative"
        assert rule == "high_or_critical_collaborative"


def test_low_medium_autonomous() -> None:
    config = load_routing()
    facts = {"privileged_account": False}
    for severity in ("low", "medium"):
        assessment = _assessment(assessed_severity=severity)
        route, rule = decide(facts, assessment, "medium", config)
        assert route == "autonomous"
        assert rule == "low_medium_autonomous"


def test_first_match_wins_and_default_review() -> None:
    config = RoutingConfig(
        version=1,
        privileged_account_patterns=["^adm"],
        rules=[
            RoutingRule(
                name="only_privileged",
                when=RuleWhen(privileged_account=True),
                route="collaborative",
            )
        ],
        default_route="review",
    )
    facts = {"privileged_account": False}
    assessment = _assessment(assessed_severity="medium", recommended_route="review")
    route, rule = decide(facts, assessment, "medium", config)
    assert route == "review"
    assert rule == "default_route"


def test_unknown_when_key_fails_load(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "privileged_account_patterns": ["^adm"],
                "rules": [
                    {
                        "name": "bad",
                        "when": {"not_a_real_key": True},
                        "route": "review",
                    }
                ],
                "default_route": "review",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_routing(path)


def test_model_recommended_route_is_advisory() -> None:
    config = load_routing()
    facts = {"privileged_account": True}
    assessment = _assessment(recommended_route="autonomous", assessed_severity="low")
    route, _rule = decide(facts, assessment, "low", config)
    assert route == "collaborative"