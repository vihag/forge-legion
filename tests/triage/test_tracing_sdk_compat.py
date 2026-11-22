"""Real Langfuse SDK signature compatibility for tracing_langfuse (no network)."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pytest

langfuse = pytest.importorskip("langfuse")
from langfuse import Langfuse  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "services/triage/tracing_langfuse.py"

TargetKind = Literal["client", "module", "constructor"]

# Explicit table of every Langfuse entry point tracing_langfuse.py uses.
EXPECTED_CALLS: list[tuple[str, TargetKind, dict[str, Any]]] = [
    (
        "Langfuse",
        "constructor",
        {
            "public_key": "",
            "secret_key": "",
            "base_url": "",
            "timeout": 5,
        },
    ),
    (
        "start_as_current_observation",
        "client",
        {"name": "triage", "as_type": "span", "input": {"alert_id": "x"}},
    ),
    (
        "propagate_attributes",
        "module",
        {"trace_name": "triage", "metadata": {"alert_id": "x"}},
    ),
    (
        "start_as_current_observation",
        "client",
        {"name": "fetch_alert", "as_type": "span", "input": {"keys": []}},
    ),
    (
        "get_prompt",
        "client",
        {
            "name": "triage-system",
            "label": "production",
            "type": "text",
            "fetch_timeout_seconds": 2,
            "max_retries": 1,
            "cache_ttl_seconds": 300,
        },
    ),
    (
        "start_as_current_observation",
        "client",
        {
            "name": "grok.assess",
            "as_type": "generation",
            "model": "grok-3-mini",
            "input": {"user_message": "hi"},
            "prompt": None,
        },
    ),
    (
        "propagate_attributes",
        "module",
        {
            "tags": ["route:autonomous", "outcome:triaged"],
            "metadata": {"case_id": "1"},
        },
    ),
    (
        "start_as_current_observation",
        "client",
        {"name": "finalize", "as_type": "span"},
    ),
    (
        "score_current_trace",
        "client",
        {"name": "assessment_confidence", "value": 0.5, "data_type": "NUMERIC"},
    ),
    (
        "score_current_trace",
        "client",
        {
            "name": "auto_resolve_gate",
            "value": 1,
            "data_type": "BOOLEAN",
            "comment": "rule",
        },
    ),
    ("shutdown", "client", {}),
]


def _resolve_callable(name: str, kind: TargetKind) -> Callable[..., Any]:
    if kind == "constructor":
        return Langfuse  # type: ignore[return-value]
    if kind == "module":
        fn = getattr(langfuse, name, None)
        assert fn is not None, f"langfuse.{name} missing on module"
        return fn
    method = getattr(Langfuse, name, None)
    assert method is not None, f"Langfuse.{name} missing on client"
    return method


def test_propagate_attributes_is_module_level_only() -> None:
    assert not hasattr(Langfuse, "propagate_attributes")
    assert callable(langfuse.propagate_attributes)


@pytest.mark.parametrize("name,kind,kwargs", EXPECTED_CALLS)
def test_expected_call_matches_sdk_signature(
    name: str, kind: TargetKind, kwargs: dict[str, Any]
) -> None:
    target = _resolve_callable(name, kind)
    sig = inspect.signature(target)
    for key in kwargs:
        assert key in sig.parameters, f"{kind}:{name} does not accept kwarg {key!r}"


def test_adapter_calls_only_listed_sdk_surface() -> None:
    source = ADAPTER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    allowed_client = {n for n, k, _ in EXPECTED_CALLS if k == "client"}
    allowed_module = {n for n, k, _ in EXPECTED_CALLS if k == "module"}

    client_hits: list[str] = []
    module_hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Attribute) and func.value.attr == "_client":
                if func.value.value.id == "self":  # type: ignore[attr-defined]
                    client_hits.append(func.attr)
            elif (
                isinstance(func.value, ast.Name)
                and func.value.id == "self"
                and func.attr == "_propagate_attributes"
            ):
                continue
            elif isinstance(func.value, ast.Name) and func.value.id in (
                "lf",
                "langfuse",
            ):
                if func.attr != "Langfuse":
                    module_hits.append(func.attr)

    assert "propagate_attributes" not in client_hits, (
        "must not call propagate_attributes on _client"
    )
    for attr in client_hits:
        assert attr in allowed_client, f"unexpected _client.{attr}"
    for attr in module_hits:
        assert attr in allowed_module, f"unexpected langfuse.{attr}"