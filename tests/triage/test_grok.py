"""Grok client request shape, env handling, retry, and validation."""

import json
import logging

import httpx
import pytest

from services.triage.config import ConfigError
from services.triage.grok import (
    GrokCallError,
    GrokResponse,
    HttpGrokClient,
    parse_assessment,
)
from services.triage.prompt import SYSTEM_PROMPT


def test_request_body_has_model_schema_and_temperature_0() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.update(body)
        payload = {
            "id": "chatcmpl-test",
            "model": "grok-4.3",
            "choices": [{"message": {"content": json.dumps(_minimal_assessment())}}],
            "usage": {},
        }
        return httpx.Response(200, json=payload)

    client = HttpGrokClient(
        api_key="test-key",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.assess("{}", evidence_ids={"EV-AUTH-0001"})
    finally:
        client.close()
    assert seen["model"] == "grok-3-mini"
    assert seen["temperature"] == 0
    assert seen["messages"][0]["content"] == SYSTEM_PROMPT
    response_format = seen["response_format"]
    assert isinstance(response_format, dict)
    schema = response_format["json_schema"]["schema"]
    assert "assessed_severity" in schema["properties"]


def test_api_key_only_from_env_and_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unused"})

    with pytest.raises(ConfigError):
        HttpGrokClient(
            api_key="",
            model="grok-3-mini",
            base_url="https://api.x.ai/v1",
            transport=httpx.MockTransport(handler),
        )
    assert "XAI_API_KEY" not in caplog.text
    assert "test-key" not in caplog.text


def test_http_grok_blocked_without_live_or_mock() -> None:
    with pytest.raises(RuntimeError, match="live or httpx.MockTransport"):
        HttpGrokClient(api_key="x", model="grok-3-mini", base_url="https://api.x.ai/v1")


def test_retry_once_on_5xx_not_on_4xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "down"})
        payload = {
            "id": "chatcmpl-retry",
            "model": "grok-4.3",
            "choices": [{"message": {"content": json.dumps(_minimal_assessment())}}],
            "usage": {},
        }
        return httpx.Response(200, json=payload)

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert calls["n"] == 2
    finally:
        client.close()

    calls["n"] = 0

    def handler_4xx(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler_4xx),
    )
    try:
        with pytest.raises(GrokCallError):
            client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert calls["n"] == 1
    finally:
        client.close()


def test_retry_not_on_429() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(GrokCallError):
            client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert calls["n"] == 1
    finally:
        client.close()


def test_retry_timeout_then_500_makes_exactly_two_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("timed out")
        return httpx.Response(500, json={"error": "still down"})

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(GrokCallError):
            client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert calls["n"] == 2
    finally:
        client.close()


def test_retry_500_then_timeout_makes_exactly_two_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "down"})
        raise httpx.ReadTimeout("timed out")

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(GrokCallError) as exc_info:
            client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert calls["n"] == 2
        assert not exc_info.value.is_invalid_output
        assert exc_info.value.detail == "timeout"
    finally:
        client.close()


def test_retry_once_on_timeout() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("timed out")
        payload = {
            "id": "chatcmpl-timeout-retry",
            "model": "grok-4.3",
            "choices": [{"message": {"content": json.dumps(_minimal_assessment())}}],
            "usage": {},
        }
        return httpx.Response(200, json=payload)

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert calls["n"] == 2
    finally:
        client.close()


def test_x_request_id_stored_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "chatcmpl-req-id",
            "model": "grok-4.3",
            "choices": [{"message": {"content": json.dumps(_minimal_assessment())}}],
            "usage": {},
        }
        return httpx.Response(200, json=payload, headers={"x-request-id": "req-abc-123"})

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        raw = client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert raw.request_id == "req-abc-123"
    finally:
        client.close()


def test_x_request_id_null_when_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "chatcmpl-no-req-id",
            "model": "grok-4.3",
            "choices": [{"message": {"content": json.dumps(_minimal_assessment())}}],
            "usage": {},
        }
        return httpx.Response(200, json=payload)

    client = HttpGrokClient(
        api_key="secret",
        model="grok-3-mini",
        base_url="https://api.x.ai/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        raw = client.assess("{}", evidence_ids={"EV-AUTH-0001"})
        assert raw.request_id is None
    finally:
        client.close()


def test_assessment_rejects_confidence_out_of_range() -> None:
    raw = GrokResponse(
        response_id="x",
        model_returned="grok-4.3",
        request_id=None,
        usage={},
        content=json.dumps({**_minimal_assessment(), "assessment_confidence": 1.5}),
    )
    with pytest.raises(GrokCallError):
        parse_assessment(raw, evidence_ids={"EV-AUTH-0001"})


def _minimal_assessment() -> dict[str, object]:
    return {
        "original_severity": "medium",
        "assessed_severity": "high",
        "assessment_confidence": 0.5,
        "rationale": "because",
        "evidence_references": ["EV-AUTH-0001"],
        "mitre_techniques": ["T1078"],
        "recommended_route": "review",
        "additional_evidence_needed": [],
    }