"""Pure mapping from Mock Splunk records onto Case API create models.

MITRE technique ids and ``triggered_at`` are not ``CaseCreate`` fields.
Callers should put them in the triage rationale or in evidence content.
"""

from typing import Any

from contracts.mock_splunk import Alert, SplunkEvidence, format_utc
from contracts.models import Actor, CaseCreate, EvidenceCreate

_RESERVED_CONTENT_KEYS = frozenset({"timestamp", "alert_ids", "entities"})


def alert_to_case_create(alert: Alert, actor: Actor) -> CaseCreate:
    """Copy the shared alert fields. ``assigned_analyst`` stays unset."""
    return CaseCreate(
        alert_id=alert.alert_id,
        title=alert.title,
        splunk_severity=alert.splunk_severity,
        summary=alert.summary,
        assigned_analyst=None,
        actor=actor,
    )


def evidence_to_evidence_create(ev: SplunkEvidence, actor: Actor) -> EvidenceCreate:
    """Map one Splunk record. ``ref`` is the evidence id.

    ``content`` gains ``timestamp``, ``alert_ids`` and ``entities`` around the
    source record. A source field that uses one of those names is a collision.
    """
    overlap = _RESERVED_CONTENT_KEYS.intersection(ev.content)
    if overlap:
        names = ", ".join(sorted(overlap))
        raise ValueError(f"evidence content collides with reserved keys: {names}")
    entities = ev.entities.model_dump(mode="json")
    content: dict[str, Any] = {
        "timestamp": format_utc(ev.timestamp),
        "alert_ids": list(ev.alert_ids),
        "entities": entities,
    }
    content.update(ev.content)
    return EvidenceCreate(
        source=ev.source,
        ref=ev.evidence_id,
        content=content,
        actor=actor,
    )
