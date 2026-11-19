"""Mock SOAR HTTP contract tests."""

from fastapi.testclient import TestClient

from tests.mock_soar.conftest import sample_submission


def test_first_submit_returns_201_and_soar_id(client: TestClient) -> None:
    response = client.post("/v1/submissions", json=sample_submission())
    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["soar_case_id"] == "SOAR-000001"
    assert body["case_id"] == 1


def test_identical_repeat_returns_200_same_id_no_new_row(client: TestClient) -> None:
    first = client.post("/v1/submissions", json=sample_submission()).json()
    repeat = client.post("/v1/submissions", json=sample_submission())
    assert repeat.status_code == 200
    body = repeat.json()
    assert body["created"] is False
    assert body["soar_case_id"] == first["soar_case_id"]
    listed = client.get("/v1/submissions", params={"case_id": 1}).json()["items"]
    assert len(listed) == 1


def test_conflicting_resubmission_returns_409(client: TestClient) -> None:
    client.post("/v1/submissions", json=sample_submission())
    conflict = {
        **sample_submission(),
        "disposition_id": 99,
    }
    response = client.post("/v1/submissions", json=conflict)
    assert response.status_code == 409
    assert response.json()["error"] == "conflicting_resubmission"


def test_reject_decision_returns_422_approval_required(client: TestClient) -> None:
    payload = sample_submission()
    payload["approval"]["decision"] = "reject"
    response = client.post("/v1/submissions", json=payload)
    assert response.status_code == 422
    assert response.json()["error"] == "approval_required"


def test_non_analyst_approver_returns_422_approval_required(client: TestClient) -> None:
    payload = sample_submission()
    payload["approval"]["approver"] = {"id": "bot-1", "type": "agent"}
    response = client.post("/v1/submissions", json=payload)
    assert response.status_code == 422
    assert response.json()["error"] == "approval_required"


def test_reset_clears_submissions(client: TestClient) -> None:
    client.post("/v1/submissions", json=sample_submission())
    reset = client.post("/v1/reset", json={})
    assert reset.status_code == 200
    assert reset.json()["submissions"] == 0
    after = client.post("/v1/submissions", json=sample_submission(case_id=2))
    assert after.status_code == 201
    assert after.json()["soar_case_id"] == "SOAR-000001"