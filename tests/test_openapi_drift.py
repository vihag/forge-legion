"""Committed OpenAPI documents match the live apps and list every contracted path."""

import json
from pathlib import Path

import pytest
from scripts.export_openapi import SPECS, canonical_mcp_tools, canonical_openapi

ROOT = Path(__file__).resolve().parents[1]

CASE_METHODS = {
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
    "/v1/cases/{case_id}/review-flag": {"post"},
    "/v1/events": {"get"},
}

MOCK_METHODS = {
    "/healthz": {"get"},
    "/v1/alerts": {"get"},
    "/v1/alerts/{alert_id}": {"get"},
    "/v1/alerts/{alert_id}/evidence": {"get"},
    "/v1/evidence": {"get"},
    "/v1/sources": {"get"},
    "/v1/reset": {"post"},
    "/v1/replay": {"post"},
}

CASE_GETS_WITH_422 = (
    "/v1/cases/{case_id}",
    "/v1/cases/{case_id}/evidence",
    "/v1/cases/{case_id}/history",
    "/v1/cases/{case_id}/view",
)


MOCK_SOAR_METHODS = {
    "/healthz": {"get"},
    "/v1/submissions": {"get", "post"},
    "/v1/submissions/{soar_case_id}": {"get"},
    "/v1/reset": {"post"},
}


@pytest.mark.parametrize("spec_name", ["case_api", "mock_splunk", "mock_soar"])
def test_committed_openapi_matches_app(spec_name: str) -> None:
    relative = SPECS[spec_name]
    committed = (ROOT / relative).read_text(encoding="utf-8")
    assert canonical_openapi(spec_name) == committed
    spec = json.loads(committed)
    paths = spec["paths"]
    if spec_name == "case_api":
        required = CASE_METHODS
    elif spec_name == "mock_splunk":
        required = MOCK_METHODS
    else:
        required = MOCK_SOAR_METHODS
    for path, methods in required.items():
        assert path in paths
        assert methods <= set(paths[path])
    if spec_name == "case_api":
        for path in CASE_GETS_WITH_422:
            assert "422" in paths[path]["get"]["responses"]


def test_committed_mcp_tools_match_server() -> None:
    committed = (ROOT / "contracts" / "mcp_tools.json").read_text(encoding="utf-8")
    assert canonical_mcp_tools() == committed
