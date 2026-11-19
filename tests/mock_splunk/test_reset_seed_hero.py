"""make reset SEED_HERO=0: Splunk reset can skip the seeded hero alert."""

from __future__ import annotations

from fastapi.testclient import TestClient

ALERT_ID = "SPL-2026-0001"


def test_reset_seed_hero_false_starts_empty(client: TestClient) -> None:
    response = client.post("/v1/reset", json={"seed_hero": False})
    assert response.status_code == 200
    body = response.json()
    assert body["alerts"] == 0
    assert body["evidence"] == 0
    assert client.get("/v1/alerts").json()["items"] == []
    assert client.get(f"/v1/alerts/{ALERT_ID}").status_code == 404


def test_reset_default_still_seeds_hero(client: TestClient) -> None:
    client.post("/v1/reset", json={"seed_hero": False})
    response = client.post("/v1/reset", json={})
    assert response.status_code == 200
    assert response.json()["alerts"] == 1
    listed = client.get("/v1/alerts").json()["items"]
    assert [item["alert_id"] for item in listed] == [ALERT_ID]


def test_reset_rejects_unknown_fields(client: TestClient) -> None:
    assert client.post("/v1/reset", json={"seed": False}).status_code == 422
