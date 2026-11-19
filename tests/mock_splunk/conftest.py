"""Fixed clock and TestClient for Mock Splunk."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from services.mock_splunk.app import create_app

FIXED_CLOCK_TIME = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_CLOCK_TIME


@pytest.fixture
def clock() -> Callable[[], datetime]:
    return fixed_clock


@pytest.fixture
def client(clock: Callable[[], datetime]) -> Iterator[TestClient]:
    application = create_app(clock=clock)
    with TestClient(application) as test_client:
        yield test_client
