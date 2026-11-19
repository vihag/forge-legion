"""Registered sources are fixtures, and a new source does not change the spec."""

import json
import shutil
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from services.mock_splunk.app import create_app
from services.mock_splunk.sources import REGISTRY
from services.mock_splunk.sources.base import SourceSpec
from services.mock_splunk.store import default_fixtures_dir

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "contracts" / "mock_splunk.openapi.json"


def _record_count(fixture_name: str) -> int:
    payload: object = json.loads(
        (default_fixtures_dir() / fixture_name).read_text(encoding="utf-8"),
    )
    assert isinstance(payload, dict)
    records = payload["records"]
    assert isinstance(records, list)
    return len(records)


@pytest.mark.parametrize("name", list(REGISTRY))
def test_each_registered_source_has_fixture_and_valid_records(name: str) -> None:
    spec = REGISTRY[name]
    path = default_fixtures_dir() / spec.fixture
    assert path.is_file()
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload["source"] == spec.name
    records = payload["records"]
    assert isinstance(records, list)
    assert records
    for record in records:
        assert isinstance(record, dict)
        evidence_id = record["evidence_id"]
        assert isinstance(evidence_id, str)
        assert evidence_id.startswith(spec.id_prefix)
        spec.Record.model_validate(record["content"])


def test_sources_endpoint_lists_registry(client: TestClient) -> None:
    response = client.get("/v1/sources")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["name"] for item in items] == list(REGISTRY)
    for item, name in zip(items, REGISTRY, strict=True):
        spec = REGISTRY[name]
        assert item["id_prefix"] == spec.id_prefix
        assert item["record_count"] == _record_count(spec.fixture)


def test_adding_source_needs_no_spec_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = create_app()
    committed = SPEC_PATH.read_text(encoding="utf-8")
    baseline_text = json.dumps(baseline.openapi(), indent=2) + "\n"
    assert baseline_text == committed

    fixtures = tmp_path / "fixtures"
    shutil.copytree(default_fixtures_dir(), fixtures)

    class VpnRecord(BaseModel):
        model_config = ConfigDict(extra="forbid")

        gateway: str
        user: str
        action: Literal["connect", "disconnect"]
        assigned_ip: str

    vpn = SourceSpec(
        name="vpn",
        id_prefix="EV-VPN-",
        Record=VpnRecord,
        fixture="vpn.json",
    )
    (fixtures / "vpn.json").write_text(
        json.dumps(
            {
                "source": "vpn",
                "records": [
                    {
                        "evidence_id": "EV-VPN-0001",
                        "timestamp": "2026-11-18T02:04:30Z",
                        "alert_ids": ["SPL-2026-0001"],
                        "entities": {"user": "adm.mtan", "hosts": ["VPN-GW-01"]},
                        "content": {
                            "gateway": "VPN-GW-01",
                            "user": "adm.mtan",
                            "action": "connect",
                            "assigned_ip": "10.20.0.5",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.mock_splunk.sources.REGISTRY",
        {**REGISTRY, vpn.name: vpn},
    )
    extra = create_app(fixtures_dir=fixtures)
    assert json.dumps(extra.openapi(), indent=2) + "\n" == committed
    with TestClient(extra) as client:
        listed = client.get("/v1/sources").json()["items"]
        assert [item["name"] for item in listed] == [*REGISTRY.keys(), "vpn"]
        vpn_row = listed[-1]
        assert vpn_row["id_prefix"] == "EV-VPN-"
        assert vpn_row["record_count"] == 1
        evidence = client.get(
            "/v1/alerts/SPL-2026-0001/evidence",
            params={"source": "vpn"},
        )
        assert evidence.status_code == 200
        assert [item["evidence_id"] for item in evidence.json()["items"]] == ["EV-VPN-0001"]
