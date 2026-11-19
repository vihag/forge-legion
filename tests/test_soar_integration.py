"""Case API ↔ Mock SOAR integration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from services.case_api.app import _resolve_soar_client, create_app
from services.case_api.soar import HttpSoarClient
from services.mock_soar.app import create_app as create_soar_app
from tests.case_api.support import decide, submit, walk_to_proposed
from tests.mcp_adapter.asgi_transport import FastApiTestTransport


def test_case_api_uses_http_soar_client_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOAR_CLIENT", "http")
    monkeypatch.setenv("MOCK_SOAR_URL", "http://soar.test")
    client = _resolve_soar_client()
    assert isinstance(client, HttpSoarClient)


def test_happy_path_approved_case_gets_mock_soar_id(tmp_path: Path) -> None:
    db_path = tmp_path / "cases.db"
    soar_app = create_soar_app(db_path=":memory:")
    soar_client = HttpSoarClient("http://soar.test", transport=FastApiTestTransport(soar_app))
    case_app = create_app(db_path=str(db_path), soar_client=soar_client)
    with TestClient(case_app) as client, TestClient(soar_app) as soar_http:
        proposed = walk_to_proposed(client, "ALT-SOAR-HAPPY")
        case_id = proposed["case_id"]
        decide(client, case_id)
        submission = submit(client, case_id)
        assert submission["soar_case_id"] == "SOAR-000001"
        events = client.get(f"/v1/cases/{case_id}/history").json()
        soar_events = [event for event in events if event["type"] == "soar.submitted"]
        assert len(soar_events) == 1
        listed = soar_http.get("/v1/submissions", params={"case_id": case_id}).json()["items"]
        assert len(listed) == 1
        assert listed[0]["soar_case_id"] == "SOAR-000001"


def test_retry_after_commit_failure_yields_same_id_once(tmp_path: Path) -> None:
    db_path = tmp_path / "cases.db"
    soar_app = create_soar_app(db_path=":memory:")
    soar_client = HttpSoarClient("http://soar.test", transport=FastApiTestTransport(soar_app))
    case_app = create_app(db_path=str(db_path), soar_client=soar_client)
    store = case_app.state.store
    real_insert = store.insert_soar
    state = {"fail": True}

    def flaky_insert(conn: sqlite3.Connection, **kwargs: Any) -> Any:
        if state["fail"]:
            state["fail"] = False
            raise sqlite3.OperationalError("simulated commit failure")
        return real_insert(conn, **kwargs)

    store.insert_soar = flaky_insert  # type: ignore[method-assign]
    with (
        TestClient(case_app, raise_server_exceptions=False) as client,
        TestClient(soar_app) as soar_http,
    ):
        proposed = walk_to_proposed(client, "ALT-SOAR-RETRY")
        case_id = proposed["case_id"]
        decide(client, case_id)
        first = client.post(
            f"/v1/cases/{case_id}/soar-submission",
            json={"actor": {"id": "soar-bot", "type": "system"}},
        )
        assert first.status_code == 500
        second = submit(client, case_id)
        assert second["soar_case_id"] == "SOAR-000001"
        listed = soar_http.get("/v1/submissions", params={"case_id": case_id}).json()["items"]
        assert len(listed) == 1
        events = client.get(f"/v1/cases/{case_id}/history").json()
        assert len([e for e in events if e["type"] == "soar.submitted"]) == 1


def test_case_api_second_submit_still_409_with_soar_case_id(tmp_path: Path) -> None:
    db_path = tmp_path / "cases.db"
    soar_app = create_soar_app(db_path=":memory:")
    client_soar = HttpSoarClient("http://soar.test", transport=FastApiTestTransport(soar_app))
    case_app = create_app(db_path=str(db_path), soar_client=client_soar)
    with TestClient(case_app) as client:
        proposed = walk_to_proposed(client, "ALT-SOAR-DUP")
        case_id = proposed["case_id"]
        decide(client, case_id)
        first = submit(client, case_id)
        second = client.post(
            f"/v1/cases/{case_id}/soar-submission",
            json={"actor": {"id": "soar-bot", "type": "system"}},
        )
        assert second.status_code == 409
        body = second.json()
        assert body["error"] == "duplicate"
        assert body["details"]["soar_case_id"] == first["soar_case_id"]


def test_soar_down_returns_502_case_stays_approved(tmp_path: Path) -> None:
    db_path = tmp_path / "cases.db"

    def down(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down", "message": "down", "details": {}})

    soar_client = HttpSoarClient("http://soar.test", transport=httpx.MockTransport(down))
    case_app = create_app(db_path=str(db_path), soar_client=soar_client)
    with TestClient(case_app) as client:
        proposed = walk_to_proposed(client, "ALT-SOAR-DOWN")
        case_id = proposed["case_id"]
        decide(client, case_id)
        response = client.post(
            f"/v1/cases/{case_id}/soar-submission",
            json={"actor": {"id": "soar-bot", "type": "system"}},
        )
        assert response.status_code == 502
        assert response.json()["error"] == "soar_unavailable"
        assert client.get(f"/v1/cases/{case_id}/view").json()["status"] == "APPROVED"


def test_soar_request_built_from_stored_decision(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/submissions":
            captured.append(json.loads(request.content.decode()))
            return httpx.Response(
                201,
                json={
                    "soar_case_id": "SOAR-000001",
                    "case_id": captured[-1]["case_id"],
                    "created": True,
                    "recorded_at": "2026-11-18T12:00:00Z",
                },
            )
        return httpx.Response(404)

    soar_client = HttpSoarClient("http://soar.test", transport=httpx.MockTransport(record))
    case_app = create_app(db_path=str(tmp_path / "cases.db"), soar_client=soar_client)
    with TestClient(case_app) as client:
        proposed = walk_to_proposed(client, "ALT-SOAR-PAYLOAD")
        case_id = proposed["case_id"]
        decision = decide(client, case_id)
        submit(client, case_id)
        assert captured
        body = captured[0]
        assert body["approval"]["decision_id"] == decision["id"]
        assert body["approval"]["decision"] == "approve"
        assert body["approval"]["approver"]["type"] == "analyst"
        assert body["approval"]["approver"]["id"] == decision["analyst_id"]