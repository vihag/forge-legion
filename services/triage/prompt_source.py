"""Resolve triage system prompt from code or Langfuse prompt management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from services.triage.prompt import SYSTEM_PROMPT
from services.triage.tracing import Tracer


@dataclass(frozen=True)
class ResolvedSystemPrompt:
    text: str
    source: Literal["code", "langfuse"]
    version: int | None
    client: Any | None = None


_CODE_PROMPT = ResolvedSystemPrompt(SYSTEM_PROMPT, "code", None, None)


def resolve_system_prompt(tracer: Tracer) -> ResolvedSystemPrompt:
    """Return the managed system prompt (cached on the tracer when Langfuse is active)."""
    return tracer.managed_system_prompt()


def code_fallback_prompt() -> ResolvedSystemPrompt:
    return _CODE_PROMPT