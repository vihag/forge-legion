"""CaseService runs with an in-memory store and FakeSoarClient, and no HTTP."""

import pytest
from contracts.models import (
    Actor,
    CaseCreate,
    CaseStatus,
    DecisionCreate,
    DispositionCreate,
    EvidenceCreate,
    FindingCreate,
    SoarSubmitRequest,
    TriageCreate,
)
from services.case_api.errors import Forbidden, IllegalTransition
from services.case_api.service import CaseService
from services.case_api.soar import FakeSoarClient
from services.case_api.store import Store


def test_service_runs_without_http() -> None:
    with Store(":memory:") as store:
        service = CaseService(store, FakeSoarClient())
        case = service.create_case(
            CaseCreate(
                alert_id="ALT-SVC",
                title="Brute force",
                splunk_severity="high",
                summary="repeated failures",
                assigned_analyst="sam",
                actor=Actor(id="splunk", type="system"),
            )
        )
        assert case.status == CaseStatus.NEW
        assert case.triage_severity is None

        with pytest.raises(IllegalTransition) as denied:
            service.record_finding(
                case.id,
                FindingCreate(
                    kind="hypothesis",
                    statement="too early",
                    evidence_ids=[],
                    actor=Actor(id="finder", type="agent"),
                ),
            )
        assert denied.value.details["from_status"] == "NEW"
        assert denied.value.details["allowed_from"] == ["TRIAGED", "INVESTIGATING", "REJECTED"]
        assert service.get_case(case.id).status == CaseStatus.NEW
        assert len(service.list_events(after_id=0, case_id=None, limit=100).items) == 1

        triaged = service.triage_case(
            case.id,
            TriageCreate(
                severity="critical",
                rationale="known pattern",
                model="grok-3",
                actor=Actor(id="triage", type="agent"),
            ),
        )
        assert triaged.status == CaseStatus.TRIAGED
        assert triaged.triage_severity == "critical"

        evidence = service.add_evidence(
            case.id,
            EvidenceCreate(
                source="mock_splunk",
                ref="search-1",
                content={"src": "10.0.0.5", "count": 40},
                actor=Actor(id="evidence", type="agent"),
            ),
        )
        finding = service.record_finding(
            case.id,
            FindingCreate(
                kind="observation",
                statement="forty failures from one host",
                evidence_ids=[evidence.id],
                confidence=0.9,
                actor=Actor(id="finder", type="agent"),
            ),
        )
        assert finding.kind == "observation"
        assert finding.evidence_ids == [evidence.id]
        assert service.get_case(case.id).status == CaseStatus.INVESTIGATING

        disposition = service.propose_disposition(
            case.id,
            DispositionCreate(
                action="contain",
                rationale="block the source",
                finding_ids=[finding.id],
                actor=Actor(id="disposition-sentinel", type="agent"),
            ),
        )
        assert service.get_case(case.id).status == CaseStatus.DISPOSITION_PROPOSED

        with pytest.raises(Forbidden):
            service.decide(
                case.id,
                DecisionCreate(
                    decision="approve",
                    rationale="a bot cannot approve",
                    actor=Actor(id="bot", type="agent"),
                ),
            )
        assert service.get_case(case.id).status == CaseStatus.DISPOSITION_PROPOSED

        decision = service.decide(
            case.id,
            DecisionCreate(
                decision="approve",
                rationale="agreed",
                actor=Actor(id="analyst-1", type="analyst"),
            ),
        )
        assert decision.decision == "approve"
        assert decision.disposition_id == disposition.id
        assert service.get_case(case.id).status == CaseStatus.APPROVED

        submission = service.submit_to_soar(
            case.id,
            SoarSubmitRequest(actor=Actor(id="system", type="system")),
        )
        assert submission.soar_case_id == f"SOAR-{case.id}-0001"
        assert service.get_case(case.id).status == CaseStatus.SUBMITTED
        assert [event.type for event in service.list_history(case.id)] == [
            "case.ingested",
            "case.triaged",
            "evidence.added",
            "finding.recorded",
            "disposition.proposed",
            "disposition.approved",
            "soar.submitted",
        ]
