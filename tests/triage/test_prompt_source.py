"""C11 Langfuse-managed triage system prompt."""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest

from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import HttpGrokClient
from services.triage.prompt import SYSTEM_PROMPT
from services.triage.prompt_source import resolve_system_prompt
from services.triage.tracing import build_tracer, noop_tracer, triage_with_trace
from services.triage.worker import handle_triage_result
from tests.triage.test_tracing import ALERT_ID, _tracer_from_sdk
from tests.triage.test_tracing_sdk import FakeLangfuseSDK, FakeTextPromptClient

ROOT = Path(__file__).resolve().parents[2]
PUSH_SCRIPT = ROOT / "scripts/langfuse_prompt_push.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_gating_off_no_get_prompt_and_system_prompt_matches() -> None:
    tracer = noop_tracer()
    resolved = resolve_system_prompt(tracer)
    assert resolved.text == SYSTEM_PROMPT
    assert resolved.source == "code"
    assert resolved.version is None
    sdk = FakeLangfuseSDK()
    off_tracer = build_tracer({}, sdk=sdk)
    resolve_system_prompt(off_tracer)
    assert len(sdk.get_prompt_calls) == 0

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content.decode())
        seen.update(body)
        payload = {
            "id": "chatcmpl-test",
            "model": "grok-4.3",
            "choices": [
                {
                    "message": {
                        "content": __import__("json").dumps(
                            {
                                "assessed_severity": "medium",
                                "verdict": "suspicious",
                                "rationale": "ok",
                                "recommended_action": "review",
                                "assessment_confidence": 0.5,
                                "mitre_techniques": ["T1078"],
                                "evidence_references": ["EV-AUTH-0001"],
                            }
                        )
                    }
                }
            ],
            "usage": {},
        }
        return httpx.Response(200, json=payload)

    client = HttpGrokClient(
        api_key="test-key",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    ).with_system_prompt(resolved.text)
    try:
        client.assess("{}", evidence_ids={"EV-AUTH-0001"})
    finally:
        client.close()
    messages = seen["messages"]
    assert isinstance(messages, list)
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert len(getattr(sdk, "get_prompt_calls", [])) == 0


def test_langfuse_fetch_used_and_generation_linked(triage_stack: TriageDeps) -> None:
    remote = "managed prompt text unique"
    sdk = FakeLangfuseSDK()
    sdk.prompt_client = FakeTextPromptClient(prompt=remote, version=7)
    tracer = _tracer_from_sdk(sdk)
    resolved = resolve_system_prompt(tracer)
    assert resolved.text == remote
    assert resolved.source == "langfuse"
    assert resolved.version == 7
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=triage_stack.grok_client,
        routing=triage_stack.routing,
        model_requested=triage_stack.model_requested,
        tracer=tracer,
        system_prompt=resolved,
    )

    def _handle(result):  # type: ignore[no-untyped-def]
        return handle_triage_result(
            webhook=None,
            case_client=triage_stack.case_client,
            result=result,
        )

    triage_with_trace(tracer=tracer, deps=deps, alert_id=ALERT_ID, handle_result=_handle)
    assert len(sdk.get_prompt_calls) == 1
    resolve_system_prompt(tracer)
    assert len(sdk.get_prompt_calls) == 1
    gen = next(o for o in sdk.observations if o.name == "grok.assess")
    assert gen.kwargs.get("prompt") is sdk.prompt_client
    finalize_props = [p for p in sdk.propagates if "prompt_source" in p.get("metadata", {})]
    assert finalize_props
    meta = finalize_props[-1]["metadata"]
    assert meta["prompt_source"] == "langfuse"
    assert meta["prompt_version"] == "7"


@pytest.mark.parametrize(
    "failure",
    [
        "exception",
        "empty",
        "fallback",
    ],
)
def test_langfuse_failures_fall_back(
    triage_stack: TriageDeps,
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    sdk = FakeLangfuseSDK()
    if failure == "exception":
        sdk.get_prompt_exc = TimeoutError("slow")
    elif failure == "empty":
        sdk.prompt_client = FakeTextPromptClient(prompt="", version=1)
    else:
        sdk.prompt_client = FakeTextPromptClient(
            prompt=SYSTEM_PROMPT, version=1, is_fallback=True
        )
    tracer = _tracer_from_sdk(sdk)
    resolved = resolve_system_prompt(tracer)
    assert resolved.text == SYSTEM_PROMPT
    assert resolved.source == "code"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=triage_stack.grok_client,
        routing=triage_stack.routing,
        model_requested=triage_stack.model_requested,
        tracer=tracer,
        system_prompt=resolved,
    )
    run_triage(deps, ALERT_ID)
    gen = next(o for o in sdk.observations if o.name == "grok.assess")
    assert "prompt" not in gen.kwargs or gen.kwargs.get("prompt") is None


def test_get_prompt_once_over_three_alerts() -> None:
    sdk = FakeLangfuseSDK()
    sdk.prompt_client = FakeTextPromptClient(prompt=SYSTEM_PROMPT, version=2)
    tracer = _tracer_from_sdk(sdk)
    for _ in range(3):
        resolve_system_prompt(tracer)
    assert len(sdk.get_prompt_calls) == 1


def test_push_script_unchanged_skips_create(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    prompt = FakeTextPromptClient(prompt=SYSTEM_PROMPT, version=3)

    class FakeLangfuse:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def get_prompt(self, name: str, **kwargs: Any) -> FakeTextPromptClient:
            del name, kwargs
            return prompt

        def create_prompt(self, **kwargs: Any) -> None:
            raise AssertionError("create_prompt should not run")

        def shutdown(self) -> None:
            return None

    fake_mod = types.ModuleType("langfuse")
    fake_mod.Langfuse = FakeLangfuse
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    with mock.patch.dict(sys.modules, {"langfuse": fake_mod}):
        spec = importlib.util.spec_from_file_location("langfuse_prompt_push", PUSH_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.main() == 0
    out = capsys.readouterr().out.strip()
    assert out == "unchanged v3"


def test_push_script_diff_calls_create(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    prompt = FakeTextPromptClient(prompt="stale", version=1)

    class FakeLangfuse:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def get_prompt(self, name: str, **kwargs: Any) -> FakeTextPromptClient:
            del name, kwargs
            return prompt

        def create_prompt(self, **kwargs: Any) -> FakeTextPromptClient:
            assert kwargs["labels"] == ["production"]
            return FakeTextPromptClient(prompt=SYSTEM_PROMPT, version=2)

        def shutdown(self) -> None:
            return None

    fake_mod = types.ModuleType("langfuse")
    fake_mod.Langfuse = FakeLangfuse
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    with mock.patch.dict(sys.modules, {"langfuse": fake_mod}):
        spec = importlib.util.spec_from_file_location("langfuse_prompt_push", PUSH_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.main() == 0
    out = capsys.readouterr().out.strip()
    assert out == "pushed v2"


def test_push_script_missing_env_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    result = subprocess.run(
        [sys.executable, str(PUSH_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={k: v for k, v in os.environ.items() if not k.startswith("LANGFUSE_")},
    )
    assert result.returncode != 0
    err = result.stderr
    assert "LANGFUSE_PUBLIC_KEY: empty" in err
    assert "LANGFUSE_SECRET_KEY: empty" in err
    assert "LANGFUSE_HOST: empty" in err