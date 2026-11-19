"""Grok system prompt and deterministic user message builder."""

import json
from typing import Any

from contracts.mock_splunk import Alert

SYSTEM_PROMPT = """You are a security operations triage assistant.

Assess security severity using only the supplied evidence. Cite evidence_ids in
evidence_references. Keep assessment_confidence separate from Splunk's original
severity label in original_severity. Flag any missing evidence in
additional_evidence_needed. Return only JSON that matches the provided schema."""


def build_user_message(alert: Alert, facts: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    """Emit deterministic JSON with sorted keys."""
    payload = {
        "alert": alert.model_dump(mode="json"),
        "evidence": sorted(evidence, key=lambda item: item["evidence_id"]),
        "facts": _sorted_dict(facts),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sorted_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}