"""Application factory. ``app`` is created on first access so imports stay side-effect free."""

from pathlib import Path

from fastapi import FastAPI

from services.mock_splunk.routes import register_routes
from services.mock_splunk.store import Clock, SplunkStore


def create_app(
    fixtures_dir: Path | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build Mock Splunk. ``clock`` is used only for replay ``emitted_at``."""
    store = SplunkStore(fixtures_dir=fixtures_dir, clock=clock)
    app = FastAPI(
        title="FORGE Mock Splunk",
        version="1.0.0",
        description="Read-only mock Splunk (/v1), plus reset and replay.",
    )
    app.state.store = store
    register_routes(app)
    return app


def __getattr__(name: str) -> FastAPI:
    # uvicorn services.mock_splunk.app:app loads this. Import alone loads no fixtures.
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
