"""Grok system prompt and deterministic user message builder."""

import json
from typing import Any

from contracts.mock_splunk import Alert

SYSTEM_PROMPT = """You are a security operations triage assistant.

Assess security severity using only the supplied evidence. Cite evidence_ids in
evidence_references. Keep assessment_confidence separate from Splunk's original
severity label in original_severity. Flag any missing evidence in
additional_evidence_needed. The rationale field must begin with one plain-English
sentence stating who is involved, what happened, where it occurred, and why it
matters for the organization, before any technical detail. Return only JSON that
matches the provided schema."""


def build_user_message(alert: Alert, facts: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    """Emit deterministic JSON with sorted keys."""
    payload = {
        "alert": alert.model_dump(mode="json"),
        "alert_plain_english_lead": _plain_english_lead(alert),
        "evidence": sorted(evidence, key=lambda item: item["evidence_id"]),
        "facts": _sorted_dict(facts),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _plain_english_lead(alert: Alert) -> str:
    user = alert.entities.user or "an unknown user"
    if alert.entities.hosts:
        where = alert.entities.hosts[0]
    elif alert.entities.src_country is not None:
        where = f"country {alert.entities.src_country}"
    else:
        where = "the environment"
    summary = alert.summary.strip().split(".")[0].strip()
    if summary:
        what = summary
    else:
        what = alert.title
    return (
        f"{user} is involved in {what} affecting {where}; "
        f"Splunk originally rated it {alert.splunk_severity}."
    )


def _sorted_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}