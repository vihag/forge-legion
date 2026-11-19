"""Application factory for Mock SOAR."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.mock_soar.routes import register_routes
from services.mock_soar.service import MockSoarService
from services.mock_soar.store import SoarStore


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    yield
    store: object = getattr(application.state, "store", None)
    if isinstance(store, SoarStore):
        store.close()


def create_app(db_path: str | None = None) -> FastAPI:
    resolved = (
        db_path if db_path is not None else os.environ.get("SOAR_DB_PATH", "./data/soar.db")
    )
    store = SoarStore(resolved)
    service = MockSoarService(store)
    app = FastAPI(
        title="FORGE Mock SOAR",
        version="1.0.0",
        description="Mock SOAR submission API.",
        lifespan=_lifespan,
    )
    app.state.store = store
    app.state.service = service
    register_routes(app)
    return app


def __getattr__(name: str) -> FastAPI:
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")