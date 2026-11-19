"""Grok chat-completions client and assessment parsing."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from contracts.triage import GrokAssessment
from services.triage.config import ConfigError
from services.triage.prompt import SYSTEM_PROMPT

MITRE_RE = re.compile(r"^T\d{4}(\.\d{3})?$")


@dataclass(frozen=True)
class GrokResponse:
    response_id: str
    model_returned: str
    request_id: str | None
    usage: dict[str, Any]
    content: str


class GrokClient(Protocol):
    def assess(self, user_message: str, *, evidence_ids: set[str]) -> GrokResponse: ...


class GrokCallError(Exception):
    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail

    @property
    def is_invalid_output(self) -> bool:
        return str(self) == "invalid model output"


class HttpGrokClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ConfigError("XAI_API_KEY is not set")
        self._api_key = api_key
        self._model = model
        self._client = httpx.Client(base_url=base_url, transport=transport, timeout=30.0)

    def close(self) -> None:
        self._client.close()

    def assess(self, user_message: str, *, evidence_ids: set[str]) -> GrokResponse:
        del evidence_ids
        body = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "triage_assessment",
                    "strict": True,
                    "schema": GrokAssessment.model_json_schema(),
                },
            },
        }
        response = self._post_with_retry(body)
        return _parse_chat_response(response)

    def _post_with_retry(self, body: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = self._client.post("/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException:
            try:
                response = self._client.post("/chat/completions", json=body, headers=headers)
            except httpx.TimeoutException as exc:
                raise GrokCallError("grok request failed", detail="timeout") from exc
        if response.status_code >= 500:
            response = self._client.post("/chat/completions", json=body, headers=headers)
        if response.status_code >= 400:
            raise GrokCallError("grok request failed", detail=response.text[:2000])
        return response


class FixtureGrokClient:
    def __init__(self, path: Path, *, model_requested: str = "grok-3-mini") -> None:
        self._path = path
        self._model_requested = model_requested

    @property
    def model_requested(self) -> str:
        return self._model_requested

    def assess(self, user_message: str, *, evidence_ids: set[str]) -> GrokResponse:
        del user_message, evidence_ids
        raw_text = self._path.read_text(encoding="utf-8")
        payload: dict[str, Any] = json.loads(raw_text)
        payload.pop("_fixture_note", None)
        return _parse_fixture_payload(payload)


def parse_assessment(raw: GrokResponse, *, evidence_ids: set[str]) -> GrokAssessment:
    try:
        parsed: object = json.loads(raw.content)
    except json.JSONDecodeError as exc:
        raise GrokCallError("invalid model output", detail="content is not json") from exc
    if not isinstance(parsed, dict):
        raise GrokCallError("invalid model output", detail="content is not an object")
    try:
        assessment = GrokAssessment.model_validate(parsed)
    except ValidationError as exc:
        raise GrokCallError("invalid model output", detail=str(exc)) from exc
    for technique in assessment.mitre_techniques:
        if not MITRE_RE.fullmatch(technique):
            raise GrokCallError("invalid model output", detail=f"bad mitre id: {technique}")
    unknown = [ref for ref in assessment.evidence_references if ref not in evidence_ids]
    if unknown:
        detail = f"unknown evidence references: {', '.join(unknown)}"
        raise GrokCallError("invalid model output", detail=detail)
    return assessment


def _parse_chat_response(response: httpx.Response) -> GrokResponse:
    data: dict[str, Any] = response.json()
    return _parse_fixture_payload(data, request_id=response.headers.get("x-request-id"))


def _parse_fixture_payload(data: dict[str, Any], request_id: str | None = None) -> GrokResponse:
    response_id_raw = data.get("id")
    if not isinstance(response_id_raw, str) or not response_id_raw.strip():
        raise GrokCallError("invalid model output", detail="missing response id")
    model_raw = data.get("model")
    if not isinstance(model_raw, str) or not model_raw.strip():
        raise GrokCallError("invalid model output", detail="missing model")
    usage_obj = data.get("usage")
    usage = usage_obj if isinstance(usage_obj, dict) else {}
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise GrokCallError("invalid model output", detail="missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise GrokCallError("invalid model output", detail="bad choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise GrokCallError("invalid model output", detail="missing message")
    content = message.get("content")
    if not isinstance(content, str):
        raise GrokCallError("invalid model output", detail="missing content")
    return GrokResponse(
        response_id=response_id_raw,
        model_returned=model_raw,
        request_id=request_id,
        usage=usage,
        content=content,
    )