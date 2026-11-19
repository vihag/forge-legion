"""The committed OpenAPI document matches the live app and lists every §1 path."""

import json
from pathlib import Path

from services.case_api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "contracts" / "openapi.json"

REQUIRED_METHODS = {
    "/healthz": {"get"},
    "/v1/cases": {"get", "post"},
    "/v1/cases/{case_id}": {"get"},
    "/v1/cases/{case_id}/evidence": {"get", "post"},
    "/v1/cases/{case_id}/findings": {"post"},
    "/v1/cases/{case_id}/history": {"get"},
    "/v1/cases/{case_id}/disposition": {"post"},
    "/v1/cases/{case_id}/decision": {"post"},
    "/v1/cases/{case_id}/soar-submission": {"post"},
    "/v1/cases/{case_id}/view": {"get"},
    "/v1/cases/{case_id}/triage": {"post"},
    "/v1/events": {"get"},
}


def test_committed_openapi_matches_app(tmp_path: Path) -> None:
    app = create_app(db_path=str(tmp_path / "cases.db"))
    live = json.dumps(app.openapi(), indent=2) + "\n"
    committed = SPEC_PATH.read_text(encoding="utf-8")
    assert live == committed
    spec = json.loads(committed)
    paths = spec["paths"]
    for path, methods in REQUIRED_METHODS.items():
        assert path in paths
        assert methods <= set(paths[path])
