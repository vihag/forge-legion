"""TestClient for Mock SOAR with in-memory database."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from services.mock_soar.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    application = create_app(db_path=":memory:")
    with TestClient(application) as test_client:
        yield test_client


def sample_submission(case_id: int = 1) -> dict[str, object]:
    return {
        "case_id": case_id,
        "alert_id": f"ALT-{case_id}",
        "disposition_id": 10,
        "action": "contain",
        "approval": {
            "decision_id": 5,
            "decision": "approve",
            "approver": {"id": "analyst-1", "type": "analyst"},
            "decided_at": "2026-11-18T12:00:00Z",
        },
        "submitted_by": {"id": "soar-bot", "type": "system"},
    }