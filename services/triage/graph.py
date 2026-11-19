"""LangGraph triage workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, StateGraph

from contracts.mock_splunk import Alert, SplunkEvidence, format_utc
from contracts.models import EvidenceCreate, ReviewFlagCreate, TriageCreate
from contracts.triage import GrokAssessment, Route, TriageResult
from services.triage.checks import compute_facts
from services.triage.clients import (
    TRIAGE_ACTOR,
    AlertNotFound,
    CaseApiClient,
    DuplicateCaseError,
    SplunkClient,
    attach_splunk_evidence,
    ingest_from_alert,
)
from services.triage.grok import GrokCallError, GrokClient, GrokResponse, parse_assessment
from services.triage.prompt import build_user_message
from services.triage.routing import RoutingConfig, decide

logger = logging.getLogger(__name__)

ReviewReason = Literal["model_call_failed", "invalid_model_output", "no_route_matched"]


class TriageState(TypedDict, total=False):
    alert_id: str
    alert: Alert
    case: dict[str, Any]
    skipped_reason: str
    splunk_evidence: list[SplunkEvidence]
    evidence_map: dict[str, int]
    facts: dict[str, Any]
    grok_raw: dict[str, Any]
    assessment: GrokAssessment
    route: Route
    rule: str
    error_reason: ReviewReason
    error_detail: str
    outcome: str
    model_requested: str
    model_returned: str | None
    response_id: str | None


@dataclass
class TriageDeps:
    case_client: CaseApiClient
    splunk_client: SplunkClient
    grok_client: GrokClient
    routing: RoutingConfig
    model_requested: str


def build_graph(deps: TriageDeps) -> StateGraph[TriageState, None, TriageState, TriageState]:
    graph = StateGraph(TriageState)

    def fetch_alert(state: TriageState) -> TriageState:
        alert = deps.splunk_client.get_alert(state["alert_id"])
        return {"alert": alert}

    def ingest_case(state: TriageState) -> TriageState:
        alert = state["alert"]
        try:
            case = ingest_from_alert(deps.case_client, alert)
            return {"case": case.model_dump(mode="json")}
        except DuplicateCaseError:
            logger.info("ingest.duplicate alert_id=%s", alert.alert_id)
            listed = deps.case_client.list_cases_by_alert_id(alert.alert_id)
            if listed.total < 1:
                raise RuntimeError("duplicate ingest but case not found") from None
            full = deps.case_client.get_case(listed.items[0].id)
            if full.status.value != "NEW":
                return {
                    "case": full.model_dump(mode="json"),
                    "outcome": "skipped_existing",
                    "skipped_reason": f"status={full.status.value}",
                }
            return {"case": full.model_dump(mode="json")}

    def fetch_evidence(state: TriageState) -> TriageState:
        alert_id = state["alert_id"]
        items = deps.splunk_client.list_evidence(alert_id).items
        return {"splunk_evidence": items}

    def attach_evidence(state: TriageState) -> TriageState:
        case_id = int(state["case"]["id"])
        existing = deps.case_client.list_evidence(case_id)
        attached_refs = {item.ref for item in existing}
        evidence_map = dict(state.get("evidence_map") or {})
        for item in state["splunk_evidence"]:
            if item.evidence_id in attached_refs:
                for ev in existing:
                    if ev.ref == item.evidence_id:
                        evidence_map[item.evidence_id] = ev.id
                continue
            created = attach_splunk_evidence(deps.case_client, case_id, item)
            evidence_map[item.evidence_id] = created.id
        return {"evidence_map": evidence_map}

    def deterministic_checks(state: TriageState) -> TriageState:
        alert = state["alert"]
        evidence = state["splunk_evidence"]
        facts = compute_facts(alert, evidence, deps.routing.privileged_account_patterns)
        return {"facts": facts}

    def grok_assess(state: TriageState) -> TriageState:
        alert = state["alert"]
        evidence_payload = _evidence_for_prompt(state["splunk_evidence"])
        user_message = build_user_message(alert, state["facts"], evidence_payload)
        try:
            raw = deps.grok_client.assess(
                user_message,
                evidence_ids=set(state.get("evidence_map", {})),
            )
        except GrokCallError as exc:
            reason: ReviewReason = (
                "invalid_model_output" if exc.is_invalid_output else "model_call_failed"
            )
            return {
                "error_reason": reason,
                "error_detail": exc.detail or str(exc),
            }
        except Exception as exc:  # noqa: BLE001 - transport failures become review
            return {
                "error_reason": "model_call_failed",
                "error_detail": str(exc)[:2000],
            }
        return {
            "grok_raw": {
                "response_id": raw.response_id,
                "model_returned": raw.model_returned,
                "request_id": raw.request_id,
                "usage": raw.usage,
                "content": raw.content,
            },
            "model_returned": raw.model_returned,
            "response_id": raw.response_id,
        }

    def validate(state: TriageState) -> TriageState:
        if state.get("error_reason"):
            return {}
        raw_dict = state["grok_raw"]
        raw = GrokResponse(
            response_id=str(raw_dict["response_id"]),
            model_returned=str(raw_dict["model_returned"]),
            request_id=raw_dict.get("request_id"),
            usage=raw_dict.get("usage", {}),
            content=str(raw_dict["content"]),
        )
        try:
            assessment = parse_assessment(raw, evidence_ids=set(state["evidence_map"]))
        except GrokCallError as exc:
            return {
                "error_reason": "invalid_model_output",
                "error_detail": exc.detail or str(exc),
            }
        return {"assessment": assessment}

    def route(state: TriageState) -> TriageState:
        if state.get("error_reason"):
            return {}
        assessment = state["assessment"]
        alert = state["alert"]
        route_value, rule = decide(
            state["facts"],
            assessment,
            alert.splunk_severity,
            deps.routing,
        )
        return {"route": route_value, "rule": rule}

    def persist_triage(state: TriageState) -> TriageState:
        if state.get("error_reason"):
            return {}
        case_id = int(state["case"]["id"])
        assessment = state["assessment"]
        alert = state["alert"]
        route_value = state["route"]
        rule = state["rule"]
        grok_raw = state["grok_raw"]
        content = {
            "model_requested": deps.model_requested,
            "model_returned": grok_raw["model_returned"],
            "response_id": grok_raw["response_id"],
            "request_id": grok_raw.get("request_id"),
            "usage": grok_raw.get("usage", {}),
            "assessment": assessment.model_dump(mode="json"),
            "facts": state["facts"],
            "route": route_value,
            "rule": rule,
            "alert_triggered_at": format_utc(alert.triggered_at),
            "alert_mitre_techniques": list(alert.mitre_techniques),
        }
        deps.case_client.add_evidence(
            case_id,
            EvidenceCreate(
                source="triage_assessment",
                ref=grok_raw["response_id"],
                content=content,
                actor=TRIAGE_ACTOR,
            ),
        )
        if route_value == "review":
            return {
                "error_reason": "no_route_matched",
                "error_detail": f"policy routed to review via {rule}",
            }
        trailer = (
            f"\n\nRoute: {route_value} (rule {rule}). "
            f"MITRE: {', '.join(alert.mitre_techniques)}. "
            f"Triggered: {format_utc(alert.triggered_at)}."
        )
        rationale = assessment.rationale + trailer
        deps.case_client.triage(
            case_id,
            TriageCreate(
                severity=assessment.assessed_severity,
                rationale=rationale,
                model=str(grok_raw["model_returned"]),
                actor=TRIAGE_ACTOR,
            ),
        )
        return {"outcome": "triaged"}

    def flag_review(state: TriageState) -> TriageState:
        reason = state.get("error_reason")
        if reason is None:
            return {}
        case_id = int(state["case"]["id"])
        detail = state.get("error_detail", "")[:2000]
        grok_raw = state.get("grok_raw") or {}
        failure_content: dict[str, Any] = {
            "reason": reason,
            "detail": detail,
            "model_requested": deps.model_requested,
        }
        if grok_raw:
            failure_content["model_returned"] = grok_raw.get("model_returned")
            excerpt = str(grok_raw.get("content", ""))[:2000]
            failure_content["raw_excerpt"] = excerpt
        deps.case_client.add_evidence(
            case_id,
            EvidenceCreate(
                source="triage_failure",
                ref=f"{reason}:{case_id}",
                content=failure_content,
                actor=TRIAGE_ACTOR,
            ),
        )
        deps.case_client.flag_review(
            case_id,
            ReviewFlagCreate(reason=reason, detail=detail, actor=TRIAGE_ACTOR),
        )
        return {"outcome": "needs_review"}

    graph.add_node("fetch_alert", fetch_alert)
    graph.add_node("ingest_case", ingest_case)
    graph.add_node("fetch_evidence", fetch_evidence)
    graph.add_node("attach_evidence", attach_evidence)
    graph.add_node("deterministic_checks", deterministic_checks)
    graph.add_node("grok_assess", grok_assess)
    graph.add_node("validate", validate)
    graph.add_node("route", route)
    graph.add_node("persist_triage", persist_triage)
    graph.add_node("flag_review", flag_review)

    graph.set_entry_point("fetch_alert")
    graph.add_edge("fetch_alert", "ingest_case")

    def after_ingest(state: TriageState) -> str:
        if state.get("outcome") == "skipped_existing":
            return END
        return "fetch_evidence"

    graph.add_conditional_edges(
        "ingest_case",
        after_ingest,
        {END: END, "fetch_evidence": "fetch_evidence"},
    )
    graph.add_edge("fetch_evidence", "attach_evidence")
    graph.add_edge("attach_evidence", "deterministic_checks")
    graph.add_edge("deterministic_checks", "grok_assess")

    def after_grok(state: TriageState) -> str:
        return "flag_review" if state.get("error_reason") else "validate"

    graph.add_conditional_edges(
        "grok_assess",
        after_grok,
        {"validate": "validate", "flag_review": "flag_review"},
    )

    def after_validate(state: TriageState) -> str:
        return "flag_review" if state.get("error_reason") else "route"

    graph.add_conditional_edges(
        "validate",
        after_validate,
        {"route": "route", "flag_review": "flag_review"},
    )
    graph.add_edge("route", "persist_triage")

    def after_persist(state: TriageState) -> str:
        return "flag_review" if state.get("error_reason") else END

    graph.add_conditional_edges(
        "persist_triage",
        after_persist,
        {END: END, "flag_review": "flag_review"},
    )
    graph.add_edge("flag_review", END)
    return graph


def run_triage(deps: TriageDeps, alert_id: str) -> TriageResult:
    compiled = build_graph(deps).compile()
    initial: TriageState = {"alert_id": alert_id, "model_requested": deps.model_requested}
    try:
        raw_final = compiled.invoke(initial)
    except AlertNotFound:
        raise
    final = cast(TriageState, raw_final)
    case = final.get("case") or {}
    assessment = final.get("assessment")
    return TriageResult(
        alert_id=alert_id,
        case_id=case.get("id") if isinstance(case.get("id"), int) else None,
        outcome=_outcome(final),
        route=final.get("route"),
        rule=final.get("rule"),
        assessed_severity=None if assessment is None else assessment.assessed_severity,
        model_requested=deps.model_requested,
        model_returned=final.get("model_returned"),
        response_id=final.get("response_id"),
    )


def _outcome(final: TriageState) -> Literal["triaged", "needs_review", "skipped_existing"]:
    value = final.get("outcome", "needs_review")
    if value in ("triaged", "needs_review", "skipped_existing"):
        return cast(Literal["triaged", "needs_review", "skipped_existing"], value)
    return "needs_review"


def _evidence_for_prompt(evidence: list[SplunkEvidence]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in evidence:
        rows.append(
            {
                "evidence_id": item.evidence_id,
                "source": item.source,
                "timestamp": format_utc(item.timestamp),
                "content": item.content,
            }
        )
    return rows