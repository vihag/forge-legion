"""Case business rules: transitions, approval, duplicates, and the audit trail.

Routes, bots, and UIs call this service. They do not decide state.
"""

import os
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any, Never

from contracts.mock_soar import SoarApproval, SoarSubmissionRequest
from contracts.models import (
    Actor,
    AuditEvent,
    AutoResolveCreate,
    Case,
    CaseCreate,
    CaseList,
    CaseStatus,
    CaseView,
    Decision,
    DecisionCreate,
    DecisionSource,
    DecisionValue,
    Disposition,
    DispositionCreate,
    EventPage,
    Evidence,
    EvidenceCreate,
    Finding,
    FindingCreate,
    FindingKind,
    Metrics,
    MetricsAgent,
    MetricsAssumptions,
    MetricsCounts,
    MetricsRecentCase,
    MetricsTokens,
    ResolveTimingStats,
    ReviewFlagCreate,
    Severity,
    SoarSubmission,
    SoarSubmitRequest,
    TimingStats,
    TriageCreate,
)
from services.case_api.errors import (
    Conflict,
    DomainValidationError,
    Duplicate,
    Forbidden,
    IllegalTransition,
    InstructionMismatch,
    NoAnalystApproval,
    NotFound,
    PolicyGateFailed,
    SoarUnavailable,
)
from services.case_api.soar import SoarClient, SoarError
from services.case_api.state import (
    ADD_EVIDENCE,
    APPROVE,
    AUTO_RESOLVE,
    FLAG_REVIEW,
    PROPOSE_DISPOSITION,
    RECORD_FINDING,
    REJECT,
    SUBMIT_TO_SOAR,
    TRIAGE,
    TransitionDenied,
    check_transition,
)
from services.case_api.store import Store

COMPONENT = "case_api"
CASE_PAGE_DEFAULT = 50
CASE_PAGE_MAX = 500
EVENT_PAGE_DEFAULT = 100
EVENT_PAGE_MAX = 1000

EVENT_INGESTED = "case.ingested"
EVENT_TRIAGED = "case.triaged"
EVENT_EVIDENCE = "evidence.added"
EVENT_FINDING = "finding.recorded"
EVENT_PROPOSED = "disposition.proposed"
EVENT_APPROVED = "disposition.approved"
EVENT_REJECTED = "disposition.rejected"
EVENT_SOAR = "soar.submitted"
EVENT_REVIEW_FLAGGED = "case.review_flagged"
EVENT_AUTO_RESOLVED = "case.auto_resolved"

AUTO_RESOLVE_POLICY_ID = "autonomous-triage"
AUTO_RESOLVE_ACTOR = Actor(id="policy:autonomous-triage", type="system")
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_GATE_IDS = tuple(f"G{index}" for index in range(1, 11))


def utc_now() -> str:
    """ISO-8601 UTC timestamp with a Z suffix and second resolution."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unique_ids(ids: list[int]) -> list[int]:
    unique: list[int] = []
    seen: set[int] = set()
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _decision_effect(decision: DecisionValue) -> tuple[str, str]:
    match decision:
        case "approve":
            return APPROVE, EVENT_APPROVED
        case "reject":
            return REJECT, EVENT_REJECTED
        case _ as unreachable:
            never_decision: Never = unreachable
            raise RuntimeError(f"unhandled decision {never_decision}")


def _decision_audit_payload(
    *,
    decision_id: int,
    disposition_id: int,
    source: DecisionSource | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision_id": decision_id,
        "disposition_id": disposition_id,
    }
    if source is not None:
        payload["source"] = source.model_dump(mode="json")
    return payload


def _instruction_matches_decision(source: DecisionSource, decision: DecisionValue) -> bool:
    text = source.instruction_text.casefold()
    match decision:
        case "approve":
            return "approve" in text
        case "reject":
            return "reject" in text
        case _ as unreachable:
            never_decision: Never = unreachable
            raise RuntimeError(f"unhandled decision {never_decision}")


def _observation_missing_evidence(kind: FindingKind, evidence_ids: list[int]) -> bool:
    match kind:
        case "observation":
            return len(evidence_ids) < 1
        case "hypothesis":
            return False
        case _ as unreachable:
            never_kind: Never = unreachable
            raise RuntimeError(f"unhandled finding kind {never_kind}")


class CaseService:
    """All case state changes go through this object, one transaction at a time."""

    def __init__(self, store: Store, soar: SoarClient) -> None:
        self._store = store
        self._soar = soar

    def create_case(self, body: CaseCreate) -> Case:
        now = utc_now()
        with self._store.transaction() as conn:
            if self._store.get_case_by_alert_id(conn, body.alert_id) is not None:
                raise Duplicate(
                    "alert_id already exists",
                    details={"alert_id": body.alert_id},
                )
            try:
                case = self._store.insert_case(
                    conn,
                    alert_id=body.alert_id,
                    title=body.title,
                    summary=body.summary,
                    splunk_severity=body.splunk_severity,
                    assigned_analyst=body.assigned_analyst,
                    alert_time=body.alert_time,
                    status=CaseStatus.NEW.value,
                    created_at=now,
                    updated_at=now,
                )
            except sqlite3.IntegrityError as exc:
                raise Duplicate(
                    "alert_id already exists",
                    details={"alert_id": body.alert_id},
                ) from exc
            self._audit(
                conn,
                case_id=case.id,
                ts=now,
                event_type=EVENT_INGESTED,
                actor=body.actor,
                from_status=None,
                to_status=CaseStatus.NEW.value,
                payload={
                    "alert_id": body.alert_id,
                    "title": body.title,
                    "splunk_severity": body.splunk_severity,
                },
            )
            return case

    def triage_case(self, case_id: int, body: TriageCreate) -> Case:
        now = utc_now()
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            to_status = self._require_transition(TRIAGE, case.status)
            updated = self._store.update_triage(
                conn,
                case_id,
                severity=body.severity,
                rationale=body.rationale,
                model=body.model,
                status=to_status.value,
                updated_at=now,
                model_requested=body.model_requested,
                route=body.route,
                rule=body.rule,
                verdict=body.verdict,
                recommended_action=body.recommended_action,
                confidence=body.confidence,
                privileged_account=body.privileged_account,
                crown_jewel_asset=body.crown_jewel_asset,
                prompt_tokens=body.prompt_tokens,
                completion_tokens=body.completion_tokens,
            )
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=EVENT_TRIAGED,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload={"severity": body.severity, "model": body.model},
            )
            return updated

    def add_evidence(self, case_id: int, body: EvidenceCreate) -> Evidence:
        now = utc_now()
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            to_status = self._require_transition(ADD_EVIDENCE, case.status)
            evidence = self._store.insert_evidence(
                conn,
                case_id=case_id,
                source=body.source,
                ref=body.ref,
                content=body.content,
                added_by=body.actor.id,
                created_at=now,
            )
            self._store.update_case_status(conn, case_id, to_status.value, now)
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=EVENT_EVIDENCE,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload={
                    "evidence_id": evidence.id,
                    "source": body.source,
                    "ref": body.ref,
                },
            )
            return evidence

    def record_finding(self, case_id: int, body: FindingCreate) -> Finding:
        now = utc_now()
        evidence_ids = _unique_ids(body.evidence_ids)
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            to_status = self._require_transition(RECORD_FINDING, case.status)
            if _observation_missing_evidence(body.kind, evidence_ids):
                raise DomainValidationError(
                    "observation requires at least one evidence id",
                    details={"kind": body.kind},
                )
            known = self._store.evidence_ids(conn, case_id)
            unknown = [evidence_id for evidence_id in evidence_ids if evidence_id not in known]
            if unknown:
                raise DomainValidationError(
                    "unknown evidence id",
                    details={"evidence_ids": unknown},
                )
            finding = self._store.insert_finding(
                conn,
                case_id=case_id,
                kind=body.kind,
                statement=body.statement,
                confidence=body.confidence,
                author=body.actor,
                evidence_ids=evidence_ids,
                created_at=now,
            )
            self._store.update_case_status(conn, case_id, to_status.value, now)
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=EVENT_FINDING,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload={
                    "finding_id": finding.id,
                    "kind": body.kind,
                    "evidence_ids": evidence_ids,
                },
            )
            return finding

    def propose_disposition(self, case_id: int, body: DispositionCreate) -> Disposition:
        now = utc_now()
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            to_status = self._require_transition(PROPOSE_DISPOSITION, case.status)
            findings = self._store.list_findings(conn, case_id)
            if len(findings) < 1:
                raise Conflict(
                    "disposition requires at least one finding",
                    details={"case_id": case_id, "operation": PROPOSE_DISPOSITION},
                )
            known = {finding.id for finding in findings}
            unknown = [finding_id for finding_id in body.finding_ids if finding_id not in known]
            if unknown:
                raise DomainValidationError(
                    "unknown finding id",
                    details={"finding_ids": unknown},
                )
            self._store.supersede_open_dispositions(conn, case_id)
            disposition = self._store.insert_disposition(
                conn,
                case_id=case_id,
                action=body.action,
                rationale=body.rationale,
                finding_ids=list(body.finding_ids),
                proposed_by=body.actor.id,
                created_at=now,
            )
            self._store.update_case_status(conn, case_id, to_status.value, now)
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=EVENT_PROPOSED,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload={"disposition_id": disposition.id, "action": body.action},
            )
            return disposition

    def decide(self, case_id: int, body: DecisionCreate) -> Decision:
        operation, event_type = _decision_effect(body.decision)
        now = utc_now()
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            if body.actor.type != "analyst":
                raise Forbidden(
                    "only an analyst may approve or reject a disposition",
                    details={"actor_type": body.actor.type},
                )
            if body.source is not None and not _instruction_matches_decision(
                body.source, body.decision
            ):
                raise InstructionMismatch(
                    "instruction_text must contain the decision word",
                    details={"decision": body.decision},
                )
            to_status = self._require_transition(operation, case.status)
            disposition = self._require_open_disposition(conn, case_id)
            decision = self._store.insert_decision(
                conn,
                case_id=case_id,
                disposition_id=disposition.id,
                decision=body.decision,
                rationale=body.rationale,
                analyst_id=body.actor.id,
                created_at=now,
            )
            self._store.update_case_status(conn, case_id, to_status.value, now)
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=event_type,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload=_decision_audit_payload(
                    decision_id=decision.id,
                    disposition_id=disposition.id,
                    source=body.source,
                ),
            )
            return decision

    def submit_to_soar(self, case_id: int, body: SoarSubmitRequest) -> SoarSubmission:
        now = utc_now()
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            existing = self._store.get_soar(conn, case_id)
            if existing is not None:
                raise Duplicate(
                    "case already submitted to SOAR",
                    details={"case_id": case_id, "soar_case_id": existing.soar_case_id},
                )
            to_status = self._require_transition(SUBMIT_TO_SOAR, case.status)
            disposition = self._require_open_disposition(conn, case_id)
            decision = self._store.get_decision_for_disposition(conn, disposition.id)
            if decision is None:
                if case.status == CaseStatus.APPROVED:
                    raise NoAnalystApproval(
                        "no analyst approval stored for this case",
                        details={"case_id": case_id, "disposition_id": disposition.id},
                    )
                raise Conflict(
                    "no decision for open disposition",
                    details={"case_id": case_id, "disposition_id": disposition.id},
                )
            if decision.decision != "approve":
                raise NoAnalystApproval(
                    "disposition was not approved by an analyst",
                    details={"case_id": case_id, "decision": decision.decision},
                )
            soar_request = SoarSubmissionRequest(
                case_id=case_id,
                alert_id=case.alert_id,
                disposition_id=disposition.id,
                action=disposition.action,
                approval=SoarApproval(
                    decision_id=decision.id,
                    decision=decision.decision,
                    approver=Actor(id=decision.analyst_id, type="analyst"),
                    decided_at=decision.created_at,
                ),
                submitted_by=body.actor,
            )
            try:
                soar_case_id = self._soar.submit(soar_request)
            except SoarError as exc:
                raise SoarUnavailable(exc.message, details={"case_id": case_id}) from exc
            try:
                submission = self._store.insert_soar(
                    conn,
                    case_id=case_id,
                    disposition_id=disposition.id,
                    soar_case_id=soar_case_id,
                    submitted_by=body.actor.id,
                    created_at=now,
                )
            except sqlite3.IntegrityError as exc:
                raise Duplicate(
                    "case already submitted to SOAR",
                    details={"case_id": case_id},
                ) from exc
            self._store.update_case_status(conn, case_id, to_status.value, now)
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=EVENT_SOAR,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload={
                    "submission_id": submission.id,
                    "disposition_id": disposition.id,
                    "soar_case_id": soar_case_id,
                },
            )
            return submission

    def get_case(self, case_id: int) -> Case:
        with self._store.transaction() as conn:
            return self._require_case(conn, case_id)

    def list_cases(
        self,
        *,
        status: CaseStatus | None,
        severity: Severity | None,
        alert_id: str | None,
        limit: int,
        offset: int,
    ) -> CaseList:
        self._check_page(limit, offset, max_limit=CASE_PAGE_MAX)
        status_value = None if status is None else status.value
        with self._store.transaction() as conn:
            items, total = self._store.list_cases(
                conn,
                status=status_value,
                severity=severity,
                alert_id=alert_id,
                limit=limit,
                offset=offset,
            )
        return CaseList(items=items, total=total)

    def auto_resolve_case(self, case_id: int, body: AutoResolveCreate) -> Case:
        if body.actor.type != "system" or body.actor.id != AUTO_RESOLVE_ACTOR.id:
            raise Forbidden(
                "only the autonomous-triage policy may auto-resolve",
                details={"actor_type": body.actor.type, "actor_id": body.actor.id},
            )
        if body.policy_id != AUTO_RESOLVE_POLICY_ID:
            raise DomainValidationError(
                "unsupported policy_id",
                details={"policy_id": body.policy_id},
            )
        now = utc_now()
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            failed = self._policy_gate_failed(conn, case, body.triage_evidence_id)
            if failed:
                raise PolicyGateFailed(
                    "autonomous policy gate failed",
                    details={"failed": failed},
                )
            to_status = self._require_transition(AUTO_RESOLVE, case.status)
            updated = self._store.update_case_status(conn, case_id, to_status.value, now)
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=EVENT_AUTO_RESOLVED,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload={
                    "verdict": case.verdict,
                    "policy_id": body.policy_id,
                    "triage_evidence_id": body.triage_evidence_id,
                    "route": case.route,
                    "gate_checks": list(_GATE_IDS),
                },
            )
            return updated

    def flag_review(self, case_id: int, body: ReviewFlagCreate) -> Case:
        now = utc_now()
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            to_status = self._require_transition(FLAG_REVIEW, case.status)
            updated = self._store.update_case_status(conn, case_id, to_status.value, now)
            self._audit(
                conn,
                case_id=case_id,
                ts=now,
                event_type=EVENT_REVIEW_FLAGGED,
                actor=body.actor,
                from_status=case.status.value,
                to_status=to_status.value,
                payload={"reason": body.reason, "detail": body.detail},
            )
            return updated

    def list_evidence(self, case_id: int) -> list[Evidence]:
        with self._store.transaction() as conn:
            self._require_case(conn, case_id)
            return self._store.list_evidence(conn, case_id)

    def list_history(self, case_id: int) -> list[AuditEvent]:
        with self._store.transaction() as conn:
            self._require_case(conn, case_id)
            return self._store.list_history(conn, case_id)

    def get_view(self, case_id: int) -> CaseView:
        with self._store.transaction() as conn:
            case = self._require_case(conn, case_id)
            disposition = self._store.get_open_disposition(conn, case_id)
            decision = None
            if disposition is not None:
                decision = self._store.get_decision_for_disposition(conn, disposition.id)
            severity = (
                case.triage_severity if case.triage_severity is not None else case.splunk_severity
            )
            return CaseView(
                id=case.id,
                alert_id=case.alert_id,
                created_at=case.created_at,
                updated_at=case.updated_at,
                title=case.title,
                severity=severity,
                splunk_severity=case.splunk_severity,
                status=case.status,
                summary=case.summary,
                assigned_analyst=case.assigned_analyst,
                evidence=self._store.list_evidence(conn, case_id),
                findings=self._store.list_findings(conn, case_id),
                proposed_disposition=disposition,
                decision=decision,
                soar=self._store.get_soar(conn, case_id),
                audit=self._store.list_history(conn, case_id),
                alert_time=case.alert_time,
                model_requested=case.model_requested,
                route=case.route,
                rule=case.rule,
                verdict=case.verdict,
                recommended_action=case.recommended_action,
                confidence=case.confidence,
                privileged_account=case.privileged_account,
                crown_jewel_asset=case.crown_jewel_asset,
                prompt_tokens=case.prompt_tokens,
                completion_tokens=case.completion_tokens,
            )

    def list_events(self, *, after_id: int, case_id: int | None, limit: int) -> EventPage:
        if after_id < 0:
            raise DomainValidationError("invalid cursor", details={"after_id": after_id})
        self._check_limit(limit, max_limit=EVENT_PAGE_MAX)
        with self._store.transaction() as conn:
            items = self._store.list_events(
                conn,
                after_id=after_id,
                case_id=case_id,
                limit=limit,
            )
        next_after_id = items[-1].id if items else after_id
        return EventPage(items=items, next_after_id=next_after_id)

    def get_metrics(self, *, since: str | None) -> Metrics:
        if since is not None:
            self._parse_iso_timestamp(since)
        now = utc_now()
        agents_since = self._iso_shift(now, minutes=-15)
        with self._store.transaction() as conn:
            cases = self._store.list_cases_for_metrics(conn, since=since)
            case_ids = [case.id for case in cases]
            audit_events = self._store.list_audit_for_case_ids(conn, case_ids)
            recent_cases = self._store.list_recent_cases(conn, 20)
            agent_events = self._store.list_audit_since(conn, agents_since)
        return self._build_metrics(
            cases=cases,
            audit_events=audit_events,
            recent_cases=recent_cases,
            agent_events=agent_events,
        )

    def _parse_iso_timestamp(self, value: str) -> datetime:
        raw = value
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise DomainValidationError(
                "invalid timestamp",
                details={"since": value},
            ) from exc
        return parsed

    def _iso_shift(self, ts: str, *, minutes: int) -> str:
        dt = self._parse_iso_timestamp(ts)
        shifted = dt + timedelta(minutes=minutes)
        return shifted.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _event_start(case: Case) -> str:
        return case.alert_time if case.alert_time is not None else case.created_at

    @staticmethod
    def _seconds_delta(start: str, end: str) -> float:
        start_raw = start[:-1] + "+00:00" if start.endswith("Z") else start
        end_raw = end[:-1] + "+00:00" if end.endswith("Z") else end
        begin = datetime.fromisoformat(start_raw)
        finish = datetime.fromisoformat(end_raw)
        return (finish - begin).total_seconds()

    @staticmethod
    def _timing_stats(values: list[float]) -> TimingStats:
        if not values:
            return TimingStats(n=0, median=None, average=None)
        median = float(statistics.median(values))
        average = sum(values) / len(values)
        return TimingStats(
            n=len(values),
            median=round(median, 1),
            average=round(average, 1),
        )

    def _build_metrics(
        self,
        *,
        cases: list[Case],
        audit_events: list[AuditEvent],
        recent_cases: list[Case],
        agent_events: list[AuditEvent],
    ) -> Metrics:
        by_status = {status.value: 0 for status in CaseStatus}
        by_route = {"autonomous": 0, "collaborative": 0, "review": 0}
        auto_resolved = 0
        collaborative_open = 0
        needs_review = 0
        uplifts: dict[str, int] = {}
        downgrades = 0

        for case in cases:
            by_status[case.status.value] += 1
            if case.route is not None:
                by_route[case.route] += 1
            if case.status == CaseStatus.AUTO_RESOLVED:
                auto_resolved += 1
            if case.status == CaseStatus.TRIAGED and case.route == "collaborative":
                collaborative_open += 1
            if case.status == CaseStatus.NEEDS_REVIEW:
                needs_review += 1
            if case.triage_severity is not None:
                splunk_rank = _SEVERITY_RANK[case.splunk_severity]
                triage_rank = _SEVERITY_RANK[case.triage_severity]
                if triage_rank > splunk_rank:
                    key = f"{case.splunk_severity}->{case.triage_severity}"
                    uplifts[key] = uplifts.get(key, 0) + 1
                elif triage_rank < splunk_rank:
                    downgrades += 1

        events_by_case: dict[int, list[AuditEvent]] = {}
        for event in audit_events:
            if event.case_id is None:
                continue
            events_by_case.setdefault(event.case_id, []).append(event)

        triage_deltas: list[float] = []
        ai_only_deltas: list[float] = []
        ai_plus_human_deltas: list[float] = []

        for case in cases:
            start = self._event_start(case)
            case_events = events_by_case.get(case.id, [])
            triage_ts = next(
                (event.ts for event in case_events if event.type == EVENT_TRIAGED),
                None,
            )
            if triage_ts is not None:
                triage_deltas.append(self._seconds_delta(start, triage_ts))
            auto_ts = next(
                (event.ts for event in case_events if event.type == EVENT_AUTO_RESOLVED),
                None,
            )
            if auto_ts is not None:
                ai_only_deltas.append(self._seconds_delta(start, auto_ts))
            approve_ts = next(
                (event.ts for event in case_events if event.type == EVENT_APPROVED),
                None,
            )
            if approve_ts is not None:
                ai_plus_human_deltas.append(self._seconds_delta(start, approve_ts))

        prompt_total = 0
        completion_total = 0
        token_cases = 0
        for case in cases:
            prompt = case.prompt_tokens or 0
            completion = case.completion_tokens or 0
            if case.prompt_tokens is not None or case.completion_tokens is not None:
                token_cases += 1
            prompt_total += prompt
            completion_total += completion
        token_total = prompt_total + completion_total
        per_case_average = (
            None if token_cases == 0 else round(token_total / token_cases, 1)
        )

        cost_usd = self._metrics_cost_usd(prompt_total, completion_total)
        minutes_per_alert = int(os.environ.get("MINUTES_PER_ALERT", "15"))
        auto_enabled = os.environ.get("AUTO_RESOLVE_ENABLED", "0").strip() == "1"

        agent_map: dict[tuple[str, str], MetricsAgent] = {}
        for event in agent_events:
            agent_key = (event.actor.id, event.actor.type)
            existing = agent_map.get(agent_key)
            if existing is None:
                agent_map[agent_key] = MetricsAgent(
                    actor_id=event.actor.id,
                    type=event.actor.type,
                    events=1,
                    last_seen=event.ts,
                )
            else:
                last_seen = existing.last_seen
                if event.ts > last_seen:
                    last_seen = event.ts
                agent_map[agent_key] = MetricsAgent(
                    actor_id=existing.actor_id,
                    type=existing.type,
                    events=existing.events + 1,
                    last_seen=last_seen,
                )
        agents = sorted(agent_map.values(), key=lambda item: item.last_seen, reverse=True)

        recent = [
            MetricsRecentCase(
                id=case.id,
                alert_id=case.alert_id,
                title=case.title,
                status=case.status,
                route=case.route,
                splunk_severity=case.splunk_severity,
                triage_severity=case.triage_severity,
                verdict=case.verdict,
                updated_at=case.updated_at,
            )
            for case in recent_cases
        ]

        return Metrics(
            counts=MetricsCounts(
                total=len(cases),
                by_status=by_status,
                by_route=by_route,
                auto_resolved=auto_resolved,
                collaborative_open=collaborative_open,
                needs_review=needs_review,
            ),
            uplifts=uplifts,
            downgrades=downgrades,
            time_to_triage_s=self._timing_stats(triage_deltas),
            time_to_resolve_s=ResolveTimingStats(
                ai_only=self._timing_stats(ai_only_deltas),
                ai_plus_human=self._timing_stats(ai_plus_human_deltas),
            ),
            tokens=MetricsTokens(
                prompt=prompt_total,
                completion=completion_total,
                total=token_total,
                per_case_average=per_case_average,
            ),
            cost_usd=cost_usd,
            auto_resolve_enabled=auto_enabled,
            minutes_saved_estimate=auto_resolved * minutes_per_alert,
            assumptions=MetricsAssumptions(minutes_per_alert=minutes_per_alert),
            agents=agents,
            recent=recent,
        )

    def _metrics_cost_usd(self, prompt: int, completion: int) -> float | None:
        in_raw = os.environ.get("GROK_PRICE_INPUT_PER_MTOK", "").strip()
        out_raw = os.environ.get("GROK_PRICE_OUTPUT_PER_MTOK", "").strip()
        if not in_raw or not out_raw:
            return None
        in_price = float(in_raw)
        out_price = float(out_raw)
        total = prompt * in_price / 1_000_000 + completion * out_price / 1_000_000
        return round(total, 4)

    def _require_case(self, conn: sqlite3.Connection, case_id: int) -> Case:
        case = self._store.get_case(conn, case_id)
        if case is None:
            raise NotFound("case not found", details={"case_id": case_id})
        return case

    def _require_open_disposition(self, conn: sqlite3.Connection, case_id: int) -> Disposition:
        disposition = self._store.get_open_disposition(conn, case_id)
        if disposition is None:
            raise Conflict("no open disposition", details={"case_id": case_id})
        return disposition

    def _require_transition(self, operation: str, from_status: CaseStatus) -> CaseStatus:
        try:
            result = check_transition(operation, from_status.value)
        except TransitionDenied as exc:
            raise IllegalTransition(
                str(exc),
                details={
                    "operation": exc.operation,
                    "from_status": exc.from_status,
                    "allowed_from": list(exc.allowed_from),
                },
            ) from exc
        return CaseStatus(result)

    def _audit(
        self,
        conn: sqlite3.Connection,
        *,
        case_id: int,
        ts: str,
        event_type: str,
        actor: Actor,
        from_status: str | None,
        to_status: str | None,
        payload: dict[str, Any],
    ) -> None:
        self._store.insert_audit(
            conn,
            case_id=case_id,
            ts=ts,
            event_type=event_type,
            actor=actor,
            component=COMPONENT,
            from_status=from_status,
            to_status=to_status,
            payload=payload,
        )

    def _check_page(self, limit: int, offset: int, *, max_limit: int) -> None:
        if limit < 1 or limit > max_limit or offset < 0:
            raise DomainValidationError(
                "invalid pagination",
                details={"limit": limit, "offset": offset},
            )

    def _check_limit(self, limit: int, *, max_limit: int) -> None:
        if limit < 1 or limit > max_limit:
            raise DomainValidationError("invalid pagination", details={"limit": limit})

    def _policy_gate_failed(
        self,
        conn: sqlite3.Connection,
        case: Case,
        triage_evidence_id: int,
    ) -> list[str]:
        failed: list[str] = []
        enabled = os.environ.get("AUTO_RESOLVE_ENABLED", "0").strip() == "1"
        if not enabled:
            failed.append("G1")
        if case.route != "autonomous":
            failed.append("G2")
        if case.verdict not in ("benign", "false_positive"):
            failed.append("G3")
        if case.recommended_action in ("escalate", "contain"):
            failed.append("G4")
        if case.triage_severity not in ("low", "medium"):
            failed.append("G5")
        if _SEVERITY_RANK[case.splunk_severity] > _SEVERITY_RANK["medium"]:
            failed.append("G6")
        if case.privileged_account is True:
            failed.append("G7")
        if case.crown_jewel_asset is True:
            failed.append("G8")
        min_conf = float(os.environ.get("AUTO_RESOLVE_MIN_CONFIDENCE", "0.8"))
        if case.confidence is None or case.confidence < min_conf:
            failed.append("G9")
        evidence = self._store.get_evidence_by_id(conn, triage_evidence_id)
        if (
            evidence is None
            or evidence.case_id != case.id
            or evidence.source != "triage_assessment"
        ):
            failed.append("G10")
        return failed
