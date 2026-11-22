"""Opt-in Langfuse tracing for triage (no langfuse import in this module)."""

from __future__ import annotations

import importlib
import logging
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

if TYPE_CHECKING:
    from services.triage.prompt_source import ResolvedSystemPrompt

logger = logging.getLogger(__name__)

_TRACE_OUTCOMES = frozenset(
    {"auto_resolved", "triaged", "needs_review", "skipped_existing", "error"}
)
TraceOutcome = Literal["auto_resolved", "triaged", "needs_review", "skipped_existing", "error"]


def _env_get(env: Mapping[str, str], key: str) -> str:
    return env.get(key, os.environ.get(key, "")).strip()


def _gating_disabled_reason(env: Mapping[str, str]) -> str | None:
    enabled = _env_get(env, "LANGFUSE_ENABLED")
    pk = _env_get(env, "LANGFUSE_PUBLIC_KEY")
    sk = _env_get(env, "LANGFUSE_SECRET_KEY")
    host = _env_get(env, "LANGFUSE_HOST")
    if enabled == "1" and pk and sk and host:
        return None
    return (
        f"enabled={'1' if enabled == '1' else '0'}"
        f"|public_key={'set' if pk else 'empty'}"
        f"|secret_key={'set' if sk else 'empty'}"
        f"|host={'set' if host else 'empty'}"
    )


@dataclass
class _NodeSpanContext:
    output: dict[str, Any] | None = None


class ObservationHandle(Protocol):
    def update(self, **kwargs: Any) -> None: ...

    def end(self, **kwargs: Any) -> None: ...


class Tracer(Protocol):
    @contextmanager
    def trace_alert(self, alert_id: str) -> Iterator[None]: ...

    @contextmanager
    def node_span(
        self, name: str, *, input_summary: dict[str, Any]
    ) -> Iterator[_NodeSpanContext]: ...

    @contextmanager
    def generation_span(
        self,
        *,
        model: str,
        input_payload: dict[str, Any],
        prompt_client: Any | None = None,
    ) -> Iterator[ObservationHandle]: ...

    def finalize(
        self,
        *,
        route: str,
        outcome: TraceOutcome,
        metadata: dict[str, str],
        gate_label: str,
        assessment_confidence: float | None,
    ) -> None: ...

    def shutdown(self, *, timeout_s: float | None = None) -> None: ...

    def generation_error(self, status_message: str) -> None: ...

    def managed_system_prompt(self) -> ResolvedSystemPrompt: ...


class _NoopObservation:
    def update(self, **kwargs: Any) -> None:
        del kwargs

    def end(self, **kwargs: Any) -> None:
        del kwargs


@dataclass
class NoopTracer:
    """Tracer that performs no I/O."""

    @contextmanager
    def trace_alert(self, alert_id: str) -> Iterator[None]:
        del alert_id
        yield

    @contextmanager
    def node_span(self, name: str, *, input_summary: dict[str, Any]) -> Iterator[_NodeSpanContext]:
        del name, input_summary
        yield _NodeSpanContext()

    @contextmanager
    def generation_span(
        self,
        *,
        model: str,
        input_payload: dict[str, Any],
        prompt_client: Any | None = None,
    ) -> Iterator[ObservationHandle]:
        del model, input_payload, prompt_client
        yield _NoopObservation()

    def finalize(
        self,
        *,
        route: str,
        outcome: TraceOutcome,
        metadata: dict[str, str],
        gate_label: str,
        assessment_confidence: float | None,
    ) -> None:
        del route, outcome, metadata, gate_label, assessment_confidence

    def shutdown(self, *, timeout_s: float | None = None) -> None:
        del timeout_s

    def generation_error(self, status_message: str) -> None:
        del status_message

    def managed_system_prompt(self) -> ResolvedSystemPrompt:
        from services.triage.prompt_source import code_fallback_prompt

        return code_fallback_prompt()


_NOOP = NoopTracer()


def noop_tracer() -> Tracer:
    return _NOOP


@dataclass
class _ErrorBudget:
    consecutive: int = 0
    disabled: bool = False
    last_log_s: float = field(default_factory=time.monotonic)


def _flush_timeout(env: Mapping[str, str]) -> float:
    raw = _env_get(env, "LANGFUSE_FLUSH_TIMEOUT_S")
    if not raw:
        return 5.0
    try:
        return float(raw)
    except ValueError:
        return 5.0


def build_tracer(
    env: Mapping[str, str] | None = None,
    *,
    sdk: Any | None = None,
    import_langfuse_module: Any | None = None,
) -> Tracer:
    """Build a tracer from environment. Optional ``sdk`` injects a fake client for tests."""
    mapping: Mapping[str, str] = os.environ if env is None else env
    reason = _gating_disabled_reason(mapping)
    if reason is not None:
        logger.info("langfuse tracing: disabled (%s)", reason)
        return _NOOP
    if import_langfuse_module is False:
        logger.info("langfuse tracing: disabled (sdk not installed)")
        return _NOOP
    try:
        if sdk is not None:
            fake_sdk = importlib.import_module("tests.triage.test_tracing_sdk")
            adapter = importlib.import_module("services.triage.tracing_langfuse")
            return cast(
                Tracer,
                adapter.LangfuseTracer.from_sdk(
                    sdk,
                    propagate_attributes=fake_sdk.make_propagate_attributes(sdk),
                    flush_timeout_s=_flush_timeout(mapping),
                ),
            )
        adapter = importlib.import_module("services.triage.tracing_langfuse")
        return cast(Tracer, adapter.build_langfuse_tracer(mapping))
    except ModuleNotFoundError as exc:
        if exc.name in ("langfuse", "services.triage.tracing_langfuse"):
            logger.info("langfuse tracing: disabled (sdk not installed)")
            return _NOOP
        raise
    except ImportError:
        logger.info("langfuse tracing: disabled (sdk not installed)")
        return _NOOP


def state_summary_keys(delta: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(delta.keys())
    summary: dict[str, Any] = {"keys": keys}
    if "splunk_evidence" in delta and isinstance(delta["splunk_evidence"], list):
        summary["evidence_count"] = len(delta["splunk_evidence"])
    return summary


def state_input_summary(state: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(state.keys())
    summary: dict[str, Any] = {"keys": keys}
    evidence = state.get("splunk_evidence")
    if isinstance(evidence, list):
        summary["evidence_count"] = len(evidence)
    return summary


def str_meta(value: object | None) -> str:
    if value is None:
        return ""
    return str(value)


def finalize_metadata(result: Any, *, case: Any | None) -> dict[str, str]:
    """Build finalize metadata with string values only."""
    from contracts.triage import TriageResult

    assert isinstance(result, TriageResult)
    case_id = ""
    rule = result.rule or ""
    if case is not None and getattr(case, "id", None) is not None:
        case_id = str(case.id)
        if case.rule:
            rule = case.rule
    return {
        "case_id": case_id,
        "rule": rule or "",
        "model_requested": str_meta(result.model_requested),
        "model_returned": str_meta(result.model_returned),
        "response_id": str_meta(result.response_id),
        "request_id": "",
    }


def triage_with_trace(
    *,
    tracer: Tracer,
    deps: Any,
    alert_id: str,
    handle_result: Any,
) -> tuple[Any, str, str, Any]:
    """Run triage inside a root trace and finalize (worker / CLI path)."""
    from services.triage.graph import run_triage

    with tracer.trace_alert(alert_id):
        result = run_triage(deps, alert_id)
        handled = handle_result(result)
        if isinstance(handled, tuple) and len(handled) == 3:
            route, gate, webhook_state = handled
        else:
            route, gate = cast(tuple[str, str], handled)
            webhook_state = None
        route_value = result.route or "none"
        if route != "—":
            route_value = route
        case = None
        confidence: float | None = None
        case_status: str | None = None
        if result.case_id is not None and result.outcome != "skipped_existing":
            case = deps.case_client.get_case(result.case_id)
            case_status = case.status.value
            if case.confidence is not None:
                confidence = float(case.confidence)
        outcome = resolve_trace_outcome(
            result_outcome=result.outcome,
            case_status=case_status,
        )
        request_id = ""
        if result.case_id is not None:
            evidence = deps.case_client.list_evidence(result.case_id)
            for item in evidence:
                if item.source == "triage_assessment" and isinstance(item.content, dict):
                    rid = item.content.get("request_id")
                    if isinstance(rid, str):
                        request_id = rid
                    break
        meta = finalize_metadata(result, case=case)
        meta["request_id"] = request_id
        resolved = deps.system_prompt
        meta["prompt_source"] = resolved.source
        meta["prompt_version"] = (
            str(resolved.version) if resolved.version is not None else "none"
        )
        allowed_routes = ("autonomous", "collaborative", "review")
        route_tag = route_value if route_value in allowed_routes else "none"
        gate_label = gate if gate != "—" else "pass"
        if result.outcome == "skipped_existing":
            route_tag = "none"
            gate_label = "pass"
        tracer.finalize(
            route=route_tag,
            outcome=outcome,
            metadata=meta,
            gate_label=gate_label,
            assessment_confidence=confidence if outcome not in ("skipped_existing",) else None,
        )
        return result, route, gate, webhook_state


def resolve_trace_outcome(
    *,
    result_outcome: str,
    case_status: str | None,
) -> TraceOutcome:
    if result_outcome == "skipped_existing":
        return "skipped_existing"
    if result_outcome == "needs_review":
        return "needs_review"
    if case_status == "AUTO_RESOLVED":
        return "auto_resolved"
    if result_outcome == "triaged":
        return "triaged"
    return "needs_review"


_SHUTDOWN_LOCK = threading.Lock()
_SHUTDOWN_CALLED = False


def shutdown_tracer_once(tracer: Tracer, *, timeout_s: float | None = None) -> None:
    global _SHUTDOWN_CALLED
    with _SHUTDOWN_LOCK:
        if _SHUTDOWN_CALLED:
            return
        _SHUTDOWN_CALLED = True
    tracer.shutdown(timeout_s=timeout_s)


def reset_shutdown_once_for_tests() -> None:
    global _SHUTDOWN_CALLED
    _SHUTDOWN_CALLED = False
