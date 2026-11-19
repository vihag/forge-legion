"""HTTP checks for the seeded scenario, windows, reset, and replay."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from services.mock_splunk.app import create_app

from .conftest import fixed_clock

ALERT_ID = "SPL-2026-0001"
SEED_SOURCES = frozenset({"auth", "proxy", "edr"})
LINKED_EVIDENCE = [
    "EV-AUTH-0002",
    "EV-AUTH-0003",
    "EV-EDR-0001",
    "EV-PROXY-0001",
    "EV-EDR-0002",
    "EV-EDR-0003",
    "EV-PROXY-0002",
]
AUTH_WINDOW = {
    "source": "auth",
    "start": "2026-11-17T00:00:00Z",
    "end": "2026-11-19T00:00:00Z",
}


def _ids(body: dict[str, object], *, sources: frozenset[str] | None = None) -> list[str]:
    items = body["items"]
    assert isinstance(items, list)
    found: list[str] = []
    for item in items:
        assert isinstance(item, dict)
        if sources is not None and item["source"] not in sources:
            continue
        evidence_id = item["evidence_id"]
        assert isinstance(evidence_id, str)
        found.append(evidence_id)
    return found


def test_responses_identical_across_two_app_instances() -> None:
    with (
        TestClient(create_app(clock=fixed_clock)) as left,
        TestClient(create_app(clock=fixed_clock)) as right,
    ):
        assert left.get("/healthz").json() == {"status": "ok"}
        for path in (
            "/healthz",
            "/v1/alerts",
            f"/v1/alerts/{ALERT_ID}",
            f"/v1/alerts/{ALERT_ID}/evidence",
            "/v1/sources",
        ):
            assert left.get(path).json() == right.get(path).json()
        params = {**AUTH_WINDOW, "entity": "adm.mtan"}
        assert (
            left.get("/v1/evidence", params=params).json()
            == right.get(
                "/v1/evidence",
                params=params,
            ).json()
        )
        left_replay = left.post("/v1/replay", json={"alert_id": ALERT_ID})
        right_replay = right.post("/v1/replay", json={"alert_id": ALERT_ID})
        assert left_replay.status_code == 201
        assert left_replay.json() == right_replay.json()


def test_seed_alert_values(client: TestClient) -> None:
    response = client.get(f"/v1/alerts/{ALERT_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["alert_id"] == ALERT_ID
    assert body["seq"] == 1
    assert body["splunk_severity"] == "medium"
    assert body["mitre_techniques"] == ["T1078", "T1021"]
    assert body["triggered_at"] == "2026-11-18T02:04:00Z"
    assert body["emitted_at"] == "2026-11-18T02:04:00Z"
    assert body["replay_of"] is None
    assert body["title"] == "Privileged account login from new country: adm.mtan"
    assert body["rule_name"] == "Privileged Login From New Country"
    assert body["summary"] == (
        "Domain admin adm.mtan authenticated via VPN-GW-01 from RO (203.0.113.45); "
        "account baseline is SG."
    )
    assert body["entities"] == {
        "user": "adm.mtan",
        "src_ip": "203.0.113.45",
        "src_country": "RO",
        "hosts": ["VPN-GW-01", "JMP-01", "FIN-DB-01"],
    }


def test_seed_alert_evidence_returns_7_sorted_by_time(client: TestClient) -> None:
    response = client.get(f"/v1/alerts/{ALERT_ID}/evidence")
    assert response.status_code == 200
    body = response.json()
    assert _ids(body, sources=SEED_SOURCES) == LINKED_EVIDENCE
    items = body["items"]
    timestamps = [item["timestamp"] for item in items]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)


def test_evidence_by_alert_filtered_by_source(client: TestClient) -> None:
    edr = client.get(f"/v1/alerts/{ALERT_ID}/evidence", params={"source": "edr"})
    assert edr.status_code == 200
    assert _ids(edr.json()) == ["EV-EDR-0001", "EV-EDR-0002", "EV-EDR-0003"]
    auth = client.get(f"/v1/alerts/{ALERT_ID}/evidence", params={"source": "auth"})
    assert _ids(auth.json()) == ["EV-AUTH-0002", "EV-AUTH-0003"]
    unknown = client.get(
        f"/v1/alerts/{ALERT_ID}/evidence",
        params={"source": "no_such_source"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "unknown_source"


def test_list_alerts_paginates_with_after_seq(client: TestClient) -> None:
    assert client.post("/v1/replay", json={"alert_id": ALERT_ID}).status_code == 201
    assert client.post("/v1/replay", json={"alert_id": ALERT_ID}).status_code == 201

    first = client.get("/v1/alerts", params={"after_seq": 0, "limit": 2})
    assert first.status_code == 200
    assert [item["seq"] for item in first.json()["items"]] == [1, 2]
    assert first.json()["next_after_seq"] == 2

    second = client.get("/v1/alerts", params={"after_seq": 2, "limit": 2})
    assert [item["seq"] for item in second.json()["items"]] == [3]
    assert second.json()["next_after_seq"] == 3

    empty = client.get("/v1/alerts", params={"after_seq": 3, "limit": 2})
    assert empty.json()["items"] == []
    assert empty.json()["next_after_seq"] == 3

    medium = client.get("/v1/alerts", params={"after_seq": 0, "severity": "medium", "limit": 1})
    assert [item["seq"] for item in medium.json()["items"]] == [1]
    assert medium.json()["next_after_seq"] == 1
    rest = client.get("/v1/alerts", params={"after_seq": 1, "severity": "medium", "limit": 10})
    assert [item["seq"] for item in rest.json()["items"]] == [2, 3]
    assert client.get("/v1/alerts", params={"severity": "high"}).json()["items"] == []
    assert client.get("/v1/alerts", params={"limit": 0}).status_code == 422
    assert client.get("/v1/alerts", params={"after_seq": -1}).status_code == 422
    assert client.get("/v1/alerts", params={"severity": "nope"}).status_code == 422


def test_unknown_alert_returns_404(client: TestClient) -> None:
    missing = client.get("/v1/alerts/SPL-MISSING")
    assert missing.status_code == 404
    assert missing.json()["error"] == "alert_not_found"
    evidence = client.get("/v1/alerts/SPL-MISSING/evidence")
    assert evidence.status_code == 404
    assert evidence.json()["error"] == "alert_not_found"


def test_window_includes_baseline_login(client: TestClient) -> None:
    response = client.get("/v1/evidence", params={**AUTH_WINDOW, "entity": "adm.mtan"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert _ids(response.json()) == ["EV-AUTH-0001", "EV-AUTH-0002", "EV-AUTH-0003"]
    assert items[0]["alert_ids"] == []
    assert items[0]["content"]["src_country"] == "SG"
    assert items[0]["content"]["result"] == "success"
    assert items[0]["content"]["mfa"] == "approved"
    folded = client.get("/v1/evidence", params={**AUTH_WINDOW, "entity": "ADM.MTAN"})
    assert _ids(folded.json()) == ["EV-AUTH-0001", "EV-AUTH-0002", "EV-AUTH-0003"]
    by_ip = client.get("/v1/evidence", params={**AUTH_WINDOW, "entity": "203.0.113.45"})
    assert _ids(by_ip.json()) == ["EV-AUTH-0002", "EV-AUTH-0003"]


def test_window_is_start_inclusive_end_exclusive(client: TestClient) -> None:
    inclusive = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-18T01:58:12Z",
            "end": "2026-11-18T02:03:41Z",
        },
    )
    assert inclusive.status_code == 200
    assert _ids(inclusive.json()) == ["EV-AUTH-0002"]
    exclusive = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-18T01:58:13Z",
            "end": "2026-11-18T02:03:42Z",
        },
    )
    assert _ids(exclusive.json()) == ["EV-AUTH-0003"]


def test_window_unknown_source_returns_422(client: TestClient) -> None:
    response = client.get(
        "/v1/evidence",
        params={
            "source": "no_such_source",
            "start": "2026-11-17T00:00:00Z",
            "end": "2026-11-18T00:00:00Z",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_source"


def test_window_start_after_end_returns_422(client: TestClient) -> None:
    reversed_window = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-18T00:00:00Z",
            "end": "2026-11-17T00:00:00Z",
        },
    )
    assert reversed_window.status_code == 422
    assert reversed_window.json()["error"] == "invalid_window"
    equal = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-18T00:00:00Z",
            "end": "2026-11-18T00:00:00Z",
        },
    )
    assert equal.status_code == 422
    assert equal.json()["error"] == "invalid_window"


def test_window_over_7_days_returns_422(client: TestClient) -> None:
    exact = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-11T00:00:00Z",
            "end": "2026-11-18T00:00:00Z",
        },
    )
    assert exact.status_code == 200
    assert "EV-AUTH-0001" in _ids(exact.json())
    over = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-11T00:00:00Z",
            "end": "2026-11-18T00:00:01Z",
        },
    )
    assert over.status_code == 422
    assert over.json()["error"] == "invalid_window"


def test_window_naive_timestamp_returns_422(client: TestClient) -> None:
    naive_start = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-17T00:00:00",
            "end": "2026-11-18T00:00:00Z",
        },
    )
    assert naive_start.status_code == 422
    assert naive_start.json()["error"] == "invalid_window"
    naive_end = client.get(
        "/v1/evidence",
        params={
            "source": "auth",
            "start": "2026-11-17T00:00:00Z",
            "end": "2026-11-18T00:00:00",
        },
    )
    assert naive_end.status_code == 422
    assert naive_end.json()["error"] == "invalid_window"


def test_reset_restores_seed_after_replay(client: TestClient) -> None:
    assert client.post("/v1/replay", json={"alert_id": ALERT_ID}).status_code == 201
    assert client.get(f"/v1/alerts/{ALERT_ID}-R1").status_code == 200
    response = client.post("/v1/reset", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["seed_version"] == "c2-seed-1"
    assert body["alerts"] == 1
    sources = client.get("/v1/sources").json()["items"]
    assert body["evidence"] == sum(item["record_count"] for item in sources)
    assert client.get(f"/v1/alerts/{ALERT_ID}-R1").status_code == 404
    listed = client.get("/v1/alerts").json()["items"]
    assert [item["alert_id"] for item in listed] == [ALERT_ID]
    assert listed[0]["seq"] == 1
    again = client.post("/v1/replay", json={"alert_id": ALERT_ID})
    assert again.status_code == 201
    assert again.json()["alert_id"] == f"{ALERT_ID}-R1"
    assert again.json()["seq"] == 2


def test_reset_is_idempotent(client: TestClient) -> None:
    assert client.post("/v1/replay", json={"alert_id": ALERT_ID}).status_code == 201
    first = client.post("/v1/reset", json={})
    after_first = client.get("/v1/alerts").json()
    evidence_after_first = client.get(f"/v1/alerts/{ALERT_ID}/evidence").json()
    second = client.post("/v1/reset", json={})
    assert first.status_code == 200
    assert first.json() == second.json()
    assert client.get("/v1/alerts").json() == after_first
    assert client.get(f"/v1/alerts/{ALERT_ID}/evidence").json() == evidence_after_first
    assert client.get(f"/v1/alerts/{ALERT_ID}-R1").status_code == 404


def test_replay_emits_new_alert_with_suffix_and_next_seq(client: TestClient) -> None:
    first = client.post("/v1/replay", json={"alert_id": ALERT_ID})
    assert first.status_code == 201
    body = first.json()
    assert body["alert_id"] == f"{ALERT_ID}-R1"
    assert body["seq"] == 2
    assert body["replay_of"] == ALERT_ID
    assert body["triggered_at"] == "2026-11-18T02:04:00Z"
    assert body["splunk_severity"] == "medium"
    assert body["mitre_techniques"] == ["T1078", "T1021"]
    original = client.get(f"/v1/alerts/{ALERT_ID}").json()
    assert original["seq"] == 1
    assert original["replay_of"] is None
    second = client.post("/v1/replay", json={"alert_id": ALERT_ID})
    assert second.json()["alert_id"] == f"{ALERT_ID}-R2"
    assert second.json()["seq"] == 3
    assert second.json()["replay_of"] == ALERT_ID


def test_replay_uses_injected_clock_for_emitted_at(client: TestClient) -> None:
    response = client.post("/v1/replay", json={"alert_id": ALERT_ID})
    assert response.status_code == 201
    assert response.json()["emitted_at"] == "2026-11-18T03:00:00Z"
    assert client.get(f"/v1/alerts/{ALERT_ID}").json()["emitted_at"] == "2026-11-18T02:04:00Z"


def test_replayed_alert_evidence_matches_root(client: TestClient) -> None:
    replayed = client.post("/v1/replay", json={"alert_id": ALERT_ID}).json()
    root = client.get(f"/v1/alerts/{ALERT_ID}/evidence").json()
    copy = client.get(f"/v1/alerts/{replayed['alert_id']}/evidence").json()
    assert copy == root
    assert _ids(copy, sources=SEED_SOURCES) == LINKED_EVIDENCE


def test_replay_of_replay_resolves_to_root(client: TestClient) -> None:
    first = client.post("/v1/replay", json={"alert_id": ALERT_ID}).json()
    second = client.post("/v1/replay", json={"alert_id": first["alert_id"]})
    assert second.status_code == 201
    body = second.json()
    assert body["alert_id"] == f"{ALERT_ID}-R2"
    assert body["replay_of"] == ALERT_ID
    assert body["seq"] == 3
    root = client.get(f"/v1/alerts/{ALERT_ID}/evidence").json()
    assert client.get(f"/v1/alerts/{body['alert_id']}/evidence").json() == root


def test_replay_unknown_alert_returns_404(client: TestClient) -> None:
    missing = client.post("/v1/replay", json={"alert_id": "SPL-MISSING"})
    assert missing.status_code == 404
    assert missing.json()["error"] == "alert_not_found"
    invalid = client.post("/v1/replay", json={})
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "validation_error"
    listed = client.get("/v1/alerts").json()["items"]
    assert [item["alert_id"] for item in listed] == [ALERT_ID]


def test_clock_can_differ_between_apps() -> None:
    """A non-contract guard: two clocks must not be baked into the seed."""
    early = datetime(2026, 11, 18, 4, 0, tzinfo=UTC)
    late = datetime(2026, 11, 18, 5, 0, tzinfo=UTC)
    with (
        TestClient(create_app(clock=lambda: early)) as left,
        TestClient(create_app(clock=lambda: late)) as right,
    ):
        left_body = left.post("/v1/replay", json={"alert_id": ALERT_ID}).json()
        right_body = right.post("/v1/replay", json={"alert_id": ALERT_ID}).json()
        assert left_body["emitted_at"] == "2026-11-18T04:00:00Z"
        assert right_body["emitted_at"] == "2026-11-18T05:00:00Z"
        left_root = left.get(f"/v1/alerts/{ALERT_ID}").json()
        right_root = right.get(f"/v1/alerts/{ALERT_ID}").json()
        assert left_root == right_root
