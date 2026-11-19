"""Mock SOAR service unit tests."""

from services.mock_soar.app import create_app
from services.mock_soar.store import SoarStore
from tests.mock_soar.conftest import sample_submission


def test_soar_ids_sequential_and_deterministic_after_reset() -> None:
    app = create_app(db_path=":memory:")
    store: SoarStore = app.state.store
    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            first = client.post("/v1/submissions", json=sample_submission(case_id=1)).json()
            second = client.post(
                "/v1/submissions",
                json=sample_submission(case_id=2),
            ).json()
            assert first["soar_case_id"] == "SOAR-000001"
            assert second["soar_case_id"] == "SOAR-000002"
            client.post("/v1/reset", json={})
            again = client.post("/v1/submissions", json=sample_submission(case_id=3)).json()
            assert again["soar_case_id"] == "SOAR-000001"
    finally:
        store.close()