"""C10 Langfuse tracing QA (TR1–TR10, TR2b)."""

from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest

from services.triage.config import load_settings as load_triage_settings
from services.triage.graph import TriageDeps, run_triage
from services.triage.grok import FixtureGrokClient, HttpGrokClient
from services.triage.tracing import (
    NoopTracer,
    build_tracer,
    noop_tracer,
    reset_shutdown_once_for_tests,
    triage_with_trace,
)
from services.triage.tracing_langfuse import LangfuseTracer
from services.triage.worker import handle_triage_result
from tests.triage.test_tracing_sdk import FakeLangfuseSDK

ROOT = Path(__file__).resolve().parents[2]
ALERT_ID = "SPL-2026-0001"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SUBPROCESS_TRIAGE = """
import os
import sys
from pathlib import Path

root = Path({root!r})
sys.path.insert(0, str(root))
os.chdir(root)

# Worker import side effect
import services.triage.worker  # noqa: F401

from services.case_api.app import create_app as create_case_app
from services.mock_splunk.app import create_app as create_splunk_app
from services.triage.clients import CaseApiClient, SplunkClient
from services.triage.grok import FixtureGrokClient
from services.triage.graph import TriageDeps, run_triage
from services.triage.http_transport import InProcessTransport
from services.triage.config import load_settings

case_app = create_case_app(db_path="/tmp/c10-tr.db")
splunk_app = create_splunk_app()
splunk_app.state.store.reset()
case_client = CaseApiClient("http://case", transport=InProcessTransport(case_app))
splunk_client = SplunkClient("http://splunk", transport=InProcessTransport(splunk_app))
settings = load_settings()
fixture = root / "tests/triage/fixtures/grok_seed_ok.json"
deps = TriageDeps(
    case_client=case_client,
    splunk_client=splunk_client,
    grok_client=FixtureGrokClient(fixture, model_requested="grok-3-mini"),
    routing=settings.routing_config,
    model_requested="grok-3-mini",
)
run_triage(deps, {alert_id!r})
has_langfuse = "langfuse" in sys.modules
has_adapter = "services.triage.tracing_langfuse" in sys.modules
print(has_langfuse, has_adapter)
"""


def _enabled_env(**overrides: str) -> dict[str, str]:
    base = {
        "LANGFUSE_ENABLED": "1",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_HOST": "https://lf.example.invalid",
    }
    base.update(overrides)
    return base


def _tracer_from_sdk(sdk: FakeLangfuseSDK) -> LangfuseTracer:
    return build_tracer(_enabled_env(), sdk=sdk)  # type: ignore[return-value]


def test_tracing_disabled_no_import() -> None:
    cases = [
        {},
        {"LANGFUSE_ENABLED": "0"},
        {"LANGFUSE_ENABLED": "1", "LANGFUSE_HOST": ""},
    ]
    for extra in cases:
        env = {**os.environ, **extra}
        for key in list(env):
            if key.startswith("LANGFUSE_") and key not in extra and key != "LANGFUSE_ENABLED":
                if key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
                    env.pop(key, None)
        result = subprocess.run(
            [sys.executable, "-c", SUBPROCESS_TRIAGE.format(root=str(ROOT), alert_id=ALERT_ID)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False False"


@pytest.mark.parametrize(
    "missing",
    ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"],
)
def test_enabled_but_missing_var_stays_off(missing: str) -> None:
    env = _enabled_env()
    env[missing] = ""
    script = f"""
import logging
import os, sys
logging.basicConfig(level=logging.INFO, format="%(message)s")
sys.path.insert(0, {str(ROOT)!r})
from services.triage.tracing import build_tracer
t = build_tracer({env!r})
print(type(t).__name__, "langfuse" in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "NoopTracer False"
    unset_env = {k: v for k, v in os.environ.items() if not k.startswith("LANGFUSE_")}
    unset_env["LANGFUSE_ENABLED"] = "1"
    if missing != "LANGFUSE_PUBLIC_KEY":
        unset_env["LANGFUSE_PUBLIC_KEY"] = "pk"
    if missing != "LANGFUSE_SECRET_KEY":
        unset_env["LANGFUSE_SECRET_KEY"] = "sk"
    if missing != "LANGFUSE_HOST":
        unset_env["LANGFUSE_HOST"] = "https://h.invalid"
    proc_unset = subprocess.run(
        [sys.executable, "-c", script],
        env=unset_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc_unset.returncode == 0
    assert proc_unset.stdout.strip().startswith("NoopTracer False")
    short = missing.removeprefix("LANGFUSE_").lower()
    assert f"{short}=empty" in proc_unset.stderr


def test_tracing_gating_matrix(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    values = ["", "1"]
    keys = ["LANGFUSE_ENABLED", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"]
    for combo in itertools.product(values, repeat=4):
        env = dict(zip(keys, combo, strict=True))
        if env["LANGFUSE_PUBLIC_KEY"]:
            env["LANGFUSE_PUBLIC_KEY"] = "pk"
        if env["LANGFUSE_SECRET_KEY"]:
            env["LANGFUSE_SECRET_KEY"] = "sk"
        if env["LANGFUSE_HOST"]:
            env["LANGFUSE_HOST"] = "https://h.invalid"
        sdk = FakeLangfuseSDK()
        tracer = build_tracer(env, sdk=sdk)
        active = (
            env["LANGFUSE_ENABLED"] == "1"
            and env["LANGFUSE_PUBLIC_KEY"]
            and env["LANGFUSE_SECRET_KEY"]
            and env["LANGFUSE_HOST"]
        )
        if active:
            assert isinstance(tracer, LangfuseTracer)
        else:
            assert isinstance(tracer, NoopTracer)
        last = [r for r in caplog.records if "langfuse tracing:" in r.message]
        if not active and last:
            msg = last[-1].message
            assert "public_key=" in msg and "secret_key=" in msg and "host=" in msg
            assert "set" in msg or "empty" in msg
    caplog.clear()
    tracer = build_tracer(_enabled_env(), sdk=None, import_langfuse_module=False)
    assert isinstance(tracer, NoopTracer)
    assert any("sdk not installed" in r.message for r in caplog.records)


def test_trace_shape(triage_stack: TriageDeps) -> None:
    sdk = FakeLangfuseSDK()
    tracer = _tracer_from_sdk(sdk)
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=triage_stack.grok_client,
        routing=triage_stack.routing,
        model_requested=triage_stack.model_requested,
        tracer=tracer,
    )

    def _handle(result):  # type: ignore[no-untyped-def]
        return handle_triage_result(
            webhook=None,
            case_client=triage_stack.case_client,
            result=result,
        )

    triage_with_trace(tracer=tracer, deps=deps, alert_id=ALERT_ID, handle_result=_handle)
    roots = [o for o in sdk.observations if o.name == "triage"]
    assert len(roots) == 1
    assert any(p.get("trace_name") == "triage" for p in sdk.propagates)
    assert any(p.get("metadata", {}).get("alert_id") == ALERT_ID for p in sdk.propagates)
    node_names = [
        o.name
        for o in sdk.observations
        if o.as_type == "span" and o.name not in ("triage", "finalize")
    ]
    expected = [
        "fetch_alert",
        "ingest_case",
        "fetch_evidence",
        "attach_evidence",
        "deterministic_checks",
        "grok_assess",
        "validate",
        "route",
        "persist_triage",
    ]
    assert node_names == expected
    generations = [
        o for o in sdk.observations if o.name == "grok.assess" and o.as_type == "generation"
    ]
    assert len(generations) == 1
    assert any(o.name == "grok_assess" and o.as_type == "span" for o in sdk.observations)
    fin = [o for o in sdk.observations if o.name == "finalize"]
    assert len(fin) == 1
    fin_prop = [p for p in sdk.propagates if "tags" in p][-1]
    tags = fin_prop["tags"]
    assert tags[0].startswith("route:")
    assert tags[1].startswith("outcome:")
    meta = fin_prop["metadata"]
    assert set(meta.keys()) == {
        "case_id",
        "rule",
        "model_requested",
        "model_returned",
        "response_id",
        "request_id",
    }
    assert all(isinstance(v, str) for v in meta.values())


def test_generation_fields(triage_stack: TriageDeps, tmp_path: Path) -> None:
    sdk = FakeLangfuseSDK()
    tracer = _tracer_from_sdk(sdk)

    def _run(deps: TriageDeps, alert: str) -> None:
        def _handle(result):  # type: ignore[no-untyped-def]
            return handle_triage_result(webhook=None, case_client=deps.case_client, result=result)

        triage_with_trace(tracer=tracer, deps=deps, alert_id=alert, handle_result=_handle)

    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=triage_stack.grok_client,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer,
    )
    _run(deps, ALERT_ID)
    gen = next(o for o in sdk.observations if o.name == "grok.assess")
    assert gen.kwargs.get("model") == "grok-3-mini"
    assert "user_message" in gen.kwargs.get("input", {})
    last = gen.updates[-1]
    assert "output" in last
    usage = next((u.get("usage_details") for u in gen.updates if "usage_details" in u), None)
    assert usage == {"input": 1263, "output": 183, "total": 2073}
    meta = next((u.get("metadata") for u in gen.updates if "metadata" in u), {})
    assert "response_id" in meta

    from services.mock_splunk.app import create_app as create_splunk_app
    from services.mock_splunk.stream import generate, reset_registry
    from services.triage.clients import SplunkClient
    from services.triage.http_transport import InProcessTransport

    splunk_app = create_splunk_app()
    stream_splunk = SplunkClient("http://splunk", transport=InProcessTransport(splunk_app))
    stream_splunk.reset()
    reset_registry()
    stream_alert = generate(7, 77)[0][0]
    splunk_app.state.store._ingest_stream_item(stream_alert)
    stream_id = stream_alert.alert.alert_id

    sdk2 = FakeLangfuseSDK()
    tracer2 = _tracer_from_sdk(sdk2)
    grok = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
    )
    deps2 = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=stream_splunk,
        grok_client=grok,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer2,
    )
    _run(deps2, stream_id)
    gen2 = next(
        o for o in sdk2.observations if o.name == "grok.assess" and o.as_type == "generation"
    )
    err = next(u for u in gen2.updates if u.get("level") == "ERROR")
    assert err["status_message"] == "model_call_failed"

    from services.case_api.app import create_app as create_case_app
    from services.triage.clients import CaseApiClient

    bad_case_app = create_case_app(db_path=str(tmp_path / "gen-bad.db"))
    bad_case = CaseApiClient("http://case", transport=InProcessTransport(bad_case_app))
    sdk3 = FakeLangfuseSDK()
    tracer3 = _tracer_from_sdk(sdk3)
    bad = FixtureGrokClient(FIXTURES / "grok_bad_severity.json", model_requested="grok-3-mini")
    deps3 = TriageDeps(
        case_client=bad_case,
        splunk_client=triage_stack.splunk_client,
        grok_client=bad,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer3,
    )
    _run(deps3, ALERT_ID)
    gen3 = next(
        o for o in sdk3.observations if o.name == "grok.assess" and o.as_type == "generation"
    )
    err3 = next(u for u in gen3.updates if u.get("level") == "ERROR")
    assert err3["status_message"] == "invalid_model_output"


def _grok_json_with_route(route: str, confidence: float = 0.95) -> dict[str, Any]:
    base = json.loads((FIXTURES / "grok_seed_ok.json").read_text())
    content = json.loads(base["choices"][0]["message"]["content"])
    content["recommended_route"] = route
    content["assessment_confidence"] = confidence
    if route == "autonomous":
        content["assessed_severity"] = "low"
        content["verdict"] = "benign"
    base["choices"][0]["message"]["content"] = json.dumps(content)
    return base


def test_scores(triage_stack: TriageDeps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.case_api.app import create_app as create_case_app
    from services.triage.clients import CaseApiClient
    from services.triage.http_transport import InProcessTransport

    path = tmp_path / "autonomous.json"
    path.write_text(json.dumps(_grok_json_with_route("autonomous")))
    monkeypatch.setenv("AUTO_RESOLVE_ENABLED", "1")

    case_app = create_case_app(db_path=str(tmp_path / "scores.db"))
    case_client = CaseApiClient("http://case", transport=InProcessTransport(case_app))

    sdk = FakeLangfuseSDK()
    tracer = _tracer_from_sdk(sdk)
    grok = FixtureGrokClient(path, model_requested="grok-3-mini")
    deps = TriageDeps(
        case_client=case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=grok,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer,
    )

    def _handle(result):  # type: ignore[no-untyped-def]
        return handle_triage_result(webhook=None, case_client=deps.case_client, result=result)

    facts = {"privileged_account": False, "crown_jewel_asset": False}
    with mock.patch("services.triage.graph.compute_facts", return_value=facts):
        with mock.patch(
            "services.triage.graph.decide", return_value=("autonomous", "low_medium_autonomous")
        ):
            triage_with_trace(tracer=tracer, deps=deps, alert_id=ALERT_ID, handle_result=_handle)
    gate_scores = [s for s in sdk.scores if s["name"] == "auto_resolve_gate"]
    assert len(gate_scores) == 1
    assert gate_scores[0]["value"] == 1
    conf_scores = [s for s in sdk.scores if s["name"] == "assessment_confidence"]
    assert conf_scores and conf_scores[0]["value"] == pytest.approx(0.95)

    sdk_fail = FakeLangfuseSDK()
    tracer_fail = _tracer_from_sdk(sdk_fail)
    fail_case_app = create_case_app(db_path=str(tmp_path / "scores-fail.db"))
    fail_case = CaseApiClient("http://case", transport=InProcessTransport(fail_case_app))
    deps_fail = TriageDeps(
        case_client=fail_case,
        splunk_client=triage_stack.splunk_client,
        grok_client=FixtureGrokClient(path, model_requested="grok-3-mini"),
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer_fail,
    )

    def _handle_fail(_result):  # type: ignore[no-untyped-def]
        return (
            "autonomous",
            "fail:low_medium_autonomous",
            "skipped",
        )

    with mock.patch(
        "services.triage.graph.decide", return_value=("autonomous", "low_medium_autonomous")
    ):
        triage_with_trace(
            tracer=tracer_fail,
            deps=deps_fail,
            alert_id=ALERT_ID,
            handle_result=_handle_fail,
        )
    fail_scores = [s for s in sdk_fail.scores if s["name"] == "auto_resolve_gate"]
    assert fail_scores[0]["value"] == 0
    assert fail_scores[0].get("comment")

    coll_path = tmp_path / "collab.json"
    coll_path.write_text(json.dumps(_grok_json_with_route("collaborative")))
    sdk_c = FakeLangfuseSDK()
    tracer_c = _tracer_from_sdk(sdk_c)
    coll_case_app = create_case_app(db_path=str(tmp_path / "scores-collab.db"))
    coll_case = CaseApiClient("http://case", transport=InProcessTransport(coll_case_app))
    deps_c = TriageDeps(
        case_client=coll_case,
        splunk_client=triage_stack.splunk_client,
        grok_client=FixtureGrokClient(coll_path, model_requested="grok-3-mini"),
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer_c,
    )
    def _handle_collab(_result):  # type: ignore[no-untyped-def]
        return ("collaborative", "pass", "skipped")

    with mock.patch(
        "services.triage.graph.decide",
        return_value=("collaborative", "privileged_account_collaborative"),
    ):
        triage_with_trace(
            tracer=tracer_c,
            deps=deps_c,
            alert_id=ALERT_ID,
            handle_result=_handle_collab,
        )
    assert not [s for s in sdk_c.scores if s["name"] == "auto_resolve_gate"]

    sdk_skip = FakeLangfuseSDK()
    tracer_skip = _tracer_from_sdk(sdk_skip)
    skip_case_app = create_case_app(db_path=str(tmp_path / "scores-skip.db"))
    skip_case = CaseApiClient("http://case", transport=InProcessTransport(skip_case_app))
    deps_skip = TriageDeps(
        case_client=skip_case,
        splunk_client=triage_stack.splunk_client,
        grok_client=FixtureGrokClient(path, model_requested="grok-3-mini"),
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer_skip,
    )
    with mock.patch("services.triage.graph.compute_facts", return_value=facts):
        with mock.patch(
            "services.triage.graph.decide", return_value=("autonomous", "low_medium_autonomous")
        ):
            run_triage(deps_skip, ALERT_ID)
    triage_with_trace(tracer=tracer_skip, deps=deps_skip, alert_id=ALERT_ID, handle_result=_handle)
    assert sdk_skip.scores == []


def test_langfuse_outage_isolated(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from services.case_api.app import create_app as create_case_app
    from services.mock_splunk.app import create_app as create_splunk_app
    from services.mock_splunk.stream import generate, reset_registry
    from services.triage.clients import CaseApiClient, SplunkClient
    from services.triage.config import load_settings
    from services.triage.grok import FixtureGrokClient
    from services.triage.http_transport import InProcessTransport

    caplog.set_level(logging.WARNING)
    settings = load_settings()
    fixture = FIXTURES / "grok_seed_ok.json"

    stack_idx = {"n": 0}

    def _stack() -> tuple[TriageDeps, list[str]]:
        stack_idx["n"] += 1
        case_app = create_case_app(db_path=str(tmp_path / f"cases-{stack_idx['n']}.db"))
        splunk_app = create_splunk_app()
        splunk_client = SplunkClient("http://splunk", transport=InProcessTransport(splunk_app))
        case_client = CaseApiClient("http://case", transport=InProcessTransport(case_app))
        splunk_client.reset()
        reset_registry()
        items = [generate(7, 90 + i)[0][0] for i in range(3)]
        alert_ids = []
        for item in items:
            splunk_app.state.store._ingest_stream_item(item)
            alert_ids.append(item.alert.alert_id)
        grok = FixtureGrokClient(fixture, model_requested="grok-3-mini")
        deps = TriageDeps(
            case_client=case_client,
            splunk_client=splunk_client,
            grok_client=grok,
            routing=settings.routing_config,
            model_requested="grok-3-mini",
            tracer=noop_tracer(),
        )
        return deps, alert_ids

    def _run(deps: TriageDeps, tracer, alert_ids: list[str]) -> list:  # type: ignore[no-untyped-def]
        out = []

        def _handle(result):  # type: ignore[no-untyped-def]
            return handle_triage_result(webhook=None, case_client=deps.case_client, result=result)

        deps.tracer = tracer
        for aid in alert_ids:
            out.append(
                triage_with_trace(
                    tracer=tracer,
                    deps=deps,
                    alert_id=aid,
                    handle_result=_handle,
                )[0]
            )
        return out

    noop_deps, ids = _stack()
    noop_tr = noop_tracer()
    noop_results = _run(noop_deps, noop_tr, ids)

    outage_deps, ids2 = _stack()
    outage_tracer = _tracer_from_sdk(FakeLangfuseSDK(fail_after_init=True))
    outage_results = _run(outage_deps, outage_tracer, ids2)

    for n, o in zip(noop_results, outage_results, strict=True):
        assert n.model_dump() == o.model_dump()

    disabled_lines = [r for r in caplog.records if "disabled for process" in r.message]
    assert len(disabled_lines) == 1
    error_lines = [r for r in caplog.records if "langfuse tracing error:" in r.message]
    assert error_lines
    assert all("RuntimeError" in r.message for r in error_lines)


def test_shutdown_bounded(caplog: pytest.LogCaptureFixture) -> None:
    sdk = FakeLangfuseSDK()

    def slow_shutdown() -> None:
        time.sleep(30)

    sdk.shutdown = slow_shutdown  # type: ignore[method-assign]
    tracer = _tracer_from_sdk(sdk)
    start = time.monotonic()
    tracer.shutdown(timeout_s=1)
    assert time.monotonic() - start < 2.0

    reset_shutdown_once_for_tests()
    calls: list[int] = []

    def fake_shutdown(*_a: object, **_kw: object) -> None:
        calls.append(1)

    with mock.patch("services.triage.worker.build_tracer", return_value=noop_tracer()):
        with mock.patch("services.triage.worker.shutdown_tracer_once", side_effect=fake_shutdown):
            with mock.patch(
                "services.triage.worker._validate_concurrency",
                return_value=1,
            ):
                with mock.patch(
                    "services.triage.worker.load_settings",
                ) as load_settings:
                    with mock.patch(
                        "services.triage.worker._resolve_grok_client",
                    ) as grok_client:
                        with mock.patch(
                            "services.triage.worker.CaseApiClient",
                        ):
                            with mock.patch(
                                "services.triage.worker.SplunkClient",
                            ) as splunk_cls:
                                splunk_inst = splunk_cls.return_value
                                splunk_inst.list_alerts.side_effect = KeyboardInterrupt()
                                from services.triage.config import TriageSettings

                                settings = load_triage_settings()
                                load_settings.return_value = TriageSettings(
                                    xai_api_key=None,
                                    xai_model="grok-3-mini",
                                    xai_base_url="https://api.x.ai/v1",
                                    case_api_url="http://case",
                                    mock_splunk_url="http://splunk",
                                    routing_config=settings.routing_config,
                                    routing_path=settings.routing_path,
                                    grok_client="fixture",
                                    grok_fixture_path=FIXTURES / "grok_seed_ok.json",
                                )
                                grok_client.return_value.close = lambda: None
                                from services.triage.worker import run_worker

                                assert run_worker() == 0
    assert len(calls) == 1


def test_error_budget_resets_after_success() -> None:
    sdk = FakeLangfuseSDK()
    tracer = _tracer_from_sdk(sdk)
    failing = RuntimeError("sdk_flap")

    def fail_start(**_kwargs: object) -> object:
        raise failing

    original = sdk.start_as_current_observation
    sdk.start_as_current_observation = fail_start  # type: ignore[method-assign]
    for _ in range(4):
        with tracer.trace_alert(ALERT_ID):
            pass
    assert tracer._budget.consecutive == 4
    assert not tracer._budget.disabled

    sdk.start_as_current_observation = original
    with tracer.trace_alert(ALERT_ID):
        pass
    assert tracer._budget.consecutive == 0

    sdk.start_as_current_observation = fail_start  # type: ignore[method-assign]
    for _ in range(4):
        with tracer.trace_alert(ALERT_ID):
            pass
    assert not tracer._budget.disabled

    with tracer.trace_alert(ALERT_ID):
        pass
    assert tracer._budget.disabled


def test_propagate_metadata_and_tags_truncated() -> None:
    sdk = FakeLangfuseSDK()
    tracer = _tracer_from_sdk(sdk)
    long = "x" * 250
    tracer.finalize(
        route="a" * 250,
        outcome="triaged",
        metadata={
            "case_id": long,
            "rule": long,
            "model_requested": "",
            "model_returned": "",
            "response_id": "",
            "request_id": "",
        },
        gate_label="pass",
        assessment_confidence=None,
    )
    prop = sdk.propagates[-1]
    assert len(json.dumps(prop["metadata"]["case_id"], ensure_ascii=False)) <= 200
    assert len(prop["tags"][0]) == 200


def test_tracing_secrets_never_logged(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    triage_stack: TriageDeps,
) -> None:
    canary_env = {
        "LANGFUSE_ENABLED": "1",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-PK-CANARY-3b1e",
        "LANGFUSE_SECRET_KEY": "sk-lf-SK-CANARY-8a4f",
        "LANGFUSE_HOST": "https://lf-canary-77d0.invalid",
    }
    caplog.set_level(10)
    for name in (None, "langfuse", "opentelemetry", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(10)

    def _assert_clean() -> None:
        blob = capsys.readouterr().out + capsys.readouterr().err
        blob += "\n".join(r.getMessage() for r in caplog.records)
        for token in ("PK-CANARY-3b1e", "SK-CANARY-8a4f", "lf-canary-77d0"):
            assert token not in blob

    class RaisingSDK(FakeLangfuseSDK):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(fail_after_init=True, **kwargs)

        def start_as_current_observation(self, **kwargs: Any) -> Any:
            raise RuntimeError("leak pk-lf-PK-CANARY-3b1e sk-lf-SK-CANARY-8a4f lf-canary-77d0")

    tracer = build_tracer(canary_env, sdk=RaisingSDK())  # type: ignore[arg-type]
    deps = TriageDeps(
        case_client=triage_stack.case_client,
        splunk_client=triage_stack.splunk_client,
        grok_client=triage_stack.grok_client,
        routing=triage_stack.routing,
        model_requested="grok-3-mini",
        tracer=tracer,
    )
    triage_with_trace(
        tracer=tracer,
        deps=deps,
        alert_id=ALERT_ID,
        handle_result=lambda r: handle_triage_result(
            webhook=None, case_client=deps.case_client, result=r
        ),
    )
    tracer.shutdown(timeout_s=0.1)
    build_tracer({**canary_env, "LANGFUSE_ENABLED": "0"})
    _assert_clean()
