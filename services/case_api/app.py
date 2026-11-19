"""Application factory. `app` is created on first access so imports stay side-effect free."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.case_api.routes import register_routes
from services.case_api.service import CaseService
from services.case_api.soar import FakeSoarClient, HttpSoarClient, SoarClient
from services.case_api.store import Store


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    yield
    store: object = getattr(application.state, "store", None)
    if isinstance(store, Store):
        store.close()


def _resolve_soar_client() -> SoarClient:
    kind = os.environ.get("SOAR_CLIENT", "fake").lower()
    if kind == "http":
        url = os.environ.get("MOCK_SOAR_URL", "http://127.0.0.1:8002")
        return HttpSoarClient(url)
    return FakeSoarClient()


def create_app(
    db_path: str | None = None,
    soar_client: SoarClient | None = None,
) -> FastAPI:
    """Build the case API. `db_path` defaults to CASE_DB_PATH or ./data/cases.db."""
    resolved = db_path if db_path is not None else os.environ.get("CASE_DB_PATH", "./data/cases.db")
    store = Store(resolved)
    soar = soar_client if soar_client is not None else _resolve_soar_client()
    service = CaseService(store, soar)
    app = FastAPI(
        title="FORGE Case API",
        version="1.0.0",
        description="Case store and case API (/v1).",
        lifespan=_lifespan,
    )
    app.state.service = service
    app.state.store = store
    app.state.db_path = resolved
    register_routes(app)
    return app


def __getattr__(name: str) -> FastAPI:
    # uvicorn services.case_api.app:app loads this. Import alone does not create a database.
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")