"""Langfuse adapter (sole module that imports langfuse)."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from services.triage.prompt_source import ResolvedSystemPrompt, code_fallback_prompt
from services.triage.tracing import TraceOutcome, _env_get, _NodeSpanContext

logger = logging.getLogger(__name__)

_LANGFUSE_LOGGERS = ("langfuse", "opentelemetry")


class _NoopObservation:
    def update(self, **kwargs: Any) -> None:
        del kwargs

    def end(self, **kwargs: Any) -> None:
        del kwargs


_NOOP_OBSERVATION = _NoopObservation()


@dataclass
class _ErrorBudget:
    consecutive: int = 0
    disabled: bool = False
    last_log_s: float = 0.0


def _note_success(budget: _ErrorBudget) -> None:
    budget.consecutive = 0


def _rate_limited_error(exc: BaseException, budget: _ErrorBudget) -> None:
    budget.consecutive += 1
    if budget.consecutive >= 5 and not budget.disabled:
        budget.disabled = True
        logger.warning("langfuse tracing: disabled for process after consecutive errors")
    now = time.monotonic()
    if now - budget.last_log_s < 60.0:
        return
    budget.last_log_s = now
    logger.warning("langfuse tracing error: %s", type(exc).__name__)


def _configure_sdk_loggers() -> None:
    for name in _LANGFUSE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _import_langfuse() -> Any:
    import langfuse  # noqa: PLC0415

    return langfuse


def _coerce_propagate_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in metadata.items():
        text = "" if value is None else str(value)
        if len(text) > 200:
            text = text[:200]
        while len(json.dumps(text, ensure_ascii=False)) > 200:
            text = text[:-1]
        out[str(key)] = text
    return out


def _coerce_propagate_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        text = str(tag)
        if len(text) > 200:
            text = text[:200]
        out.append(text)
    return out


@dataclass
class LangfuseTracer:
    _client: Any
    _propagate_attributes: Any
    _budget: _ErrorBudget = field(default_factory=_ErrorBudget)
    _flush_timeout_s: float = 5.0
    _active_generation: Any | None = None
    _managed_prompt: ResolvedSystemPrompt | None = None

    @classmethod
    def from_sdk(
        cls,
        sdk: Any,
        *,
        propagate_attributes: Any | None = None,
        flush_timeout_s: float = 5.0,
    ) -> LangfuseTracer:
        prop = propagate_attributes
        if prop is None:
            prop = _import_langfuse().propagate_attributes
        logger.info("langfuse tracing: enabled")
        return cls(_client=sdk, _propagate_attributes=prop, _flush_timeout_s=flush_timeout_s)

    @contextmanager
    def trace_alert(self, alert_id: str) -> Iterator[None]:
        if self._budget.disabled:
            yield
            return
        try:
            obs_cm = self._client.start_as_current_observation(
                name="triage",
                as_type="span",
                input={"alert_id": alert_id},
            )
        except Exception as exc:
            _rate_limited_error(exc, self._budget)
            yield
            return
        try:
            prop_cm = self._propagate_attributes(
                trace_name="triage",
                metadata=_coerce_propagate_metadata({"alert_id": alert_id}),
            )
        except Exception as exc:
            _rate_limited_error(exc, self._budget)
            yield
            return
        with obs_cm, prop_cm:
            try:
                yield
            finally:
                _note_success(self._budget)

    @contextmanager
    def node_span(self, name: str, *, input_summary: dict[str, Any]) -> Iterator[_NodeSpanContext]:
        holder = _NodeSpanContext()
        if self._budget.disabled:
            yield holder
            return
        try:
            obs_cm = self._client.start_as_current_observation(
                name=name,
                as_type="span",
                input=input_summary,
            )
        except Exception as exc:
            _rate_limited_error(exc, self._budget)
            yield holder
            return
        with obs_cm as span:
            try:
                yield holder
                if holder.output is not None:
                    span.update(output=holder.output)
                _note_success(self._budget)
            except Exception as exc:
                span.update(level="ERROR", status_message=type(exc).__name__)
                _note_success(self._budget)
                raise

    def managed_system_prompt(self) -> ResolvedSystemPrompt:
        if self._managed_prompt is not None:
            return self._managed_prompt
        if self._budget.disabled:
            self._managed_prompt = code_fallback_prompt()
            return self._managed_prompt
        try:
            client = self._client.get_prompt(
                name="triage-system",
                label="production",
                type="text",
                fetch_timeout_seconds=2,
                max_retries=1,
                cache_ttl_seconds=300,
            )
            text = client.prompt
            if not isinstance(text, str) or not text or getattr(client, "is_fallback", False):
                raise ValueError("prompt unavailable")
            self._managed_prompt = ResolvedSystemPrompt(
                text=text,
                source="langfuse",
                version=getattr(client, "version", None),
                client=client,
            )
        except Exception as exc:
            logger.warning("triage system prompt: %s", type(exc).__name__)
            self._managed_prompt = code_fallback_prompt()
        return self._managed_prompt

    @contextmanager
    def generation_span(
        self,
        *,
        model: str,
        input_payload: dict[str, Any],
        prompt_client: Any | None = None,
    ) -> Iterator[Any]:
        if self._budget.disabled:
            yield _NOOP_OBSERVATION
            return
        gen_kwargs: dict[str, Any] = {
            "name": "grok.assess",
            "as_type": "generation",
            "model": model,
            "input": input_payload,
        }
        if prompt_client is not None:
            gen_kwargs["prompt"] = prompt_client
        try:
            obs_cm = self._client.start_as_current_observation(**gen_kwargs)
        except Exception as exc:
            _rate_limited_error(exc, self._budget)
            yield _NOOP_OBSERVATION
            return
        with obs_cm as gen:
            self._active_generation = gen
            try:
                yield gen
            finally:
                self._active_generation = None
                _note_success(self._budget)

    def generation_error(self, status_message: str) -> None:
        if self._budget.disabled or self._active_generation is None:
            return
        try:
            self._active_generation.update(level="ERROR", status_message=status_message)
            _note_success(self._budget)
        except Exception as exc:
            _rate_limited_error(exc, self._budget)

    def finalize(
        self,
        *,
        route: str,
        outcome: TraceOutcome,
        metadata: dict[str, str],
        gate_label: str,
        assessment_confidence: float | None,
    ) -> None:
        if self._budget.disabled:
            return
        try:
            with self._propagate_attributes(
                tags=_coerce_propagate_tags([f"route:{route}", f"outcome:{outcome}"]),
                metadata=_coerce_propagate_metadata(metadata),
            ):
                with self._client.start_as_current_observation(
                    name="finalize",
                    as_type="span",
                ) as fin:
                    fin.update(input={"route": route, "outcome": outcome})
            if assessment_confidence is not None:
                self._client.score_current_trace(
                    name="assessment_confidence",
                    value=assessment_confidence,
                    data_type="NUMERIC",
                )
            if route == "autonomous":
                value = 1 if gate_label == "pass" else 0
                comment = ""
                if gate_label.startswith("fail:"):
                    comment = gate_label.removeprefix("fail:")
                self._client.score_current_trace(
                    name="auto_resolve_gate",
                    value=value,
                    data_type="BOOLEAN",
                    comment=comment,
                )
            _note_success(self._budget)
        except Exception as exc:
            _rate_limited_error(exc, self._budget)

    def shutdown(self, *, timeout_s: float | None = None) -> None:
        wait = self._flush_timeout_s if timeout_s is None else timeout_s
        client = self._client

        def _run() -> None:
            try:
                client.shutdown()
            except Exception as exc:
                _rate_limited_error(exc, self._budget)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=wait)


def build_langfuse_tracer(env: Mapping[str, str]) -> LangfuseTracer:
    _configure_sdk_loggers()
    lf = _import_langfuse()
    client = lf.Langfuse(
        public_key=_env_get(env, "LANGFUSE_PUBLIC_KEY"),
        secret_key=_env_get(env, "LANGFUSE_SECRET_KEY"),
        base_url=_env_get(env, "LANGFUSE_HOST"),
        timeout=5,
    )
    from services.triage.tracing import _flush_timeout

    logger.info("langfuse tracing: enabled")
    return LangfuseTracer(
        _client=client,
        _propagate_attributes=lf.propagate_attributes,
        _flush_timeout_s=_flush_timeout(env),
    )