"""Temporary database, API client, and a case that has been triaged with evidence."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from services.case_api.app import create_app

from .support import add_evidence, ingest, triage


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    application = create_app(db_path=str(tmp_path / "cases.db"))
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def seeded_case(client: TestClient) -> dict[str, Any]:
    """A TRIAGED case that already has one piece of evidence and no findings."""
    case = ingest(client, "ALT-SEEDED")
    triaged = triage(client, case["id"])
    evidence = add_evidence(client, case["id"])
    return {"case_id": case["id"], "case": triaged, "evidence": evidence}
