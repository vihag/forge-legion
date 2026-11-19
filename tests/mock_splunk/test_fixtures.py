"""Seed fixtures validate, ids line up, and a bad fixture directory fails at load."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from services.mock_splunk.errors import FixtureError
from services.mock_splunk.sources import REGISTRY
from services.mock_splunk.store import SplunkStore, default_fixtures_dir


def test_all_fixtures_validate_against_models() -> None:
    store = SplunkStore()
    assert store.seed_version == "c2-seed-1"
    alerts = store.loaded_alerts()
    evidence = store.loaded_evidence()
    assert len(alerts) == 1
    assert len(evidence) == 8
    counts: dict[str, int] = {}
    for item in evidence:
        spec = REGISTRY[item.source]
        spec.Record.model_validate(item.content)
        counts[item.source] = counts.get(item.source, 0) + 1
    assert counts == {"auth": 3, "proxy": 2, "edr": 3}
    alert = alerts[0]
    assert alert.alert_id == "SPL-2026-0001"
    assert alert.seq == 1
    assert alert.replay_of is None
    assert alert.emitted_at == alert.triggered_at


def test_evidence_ids_unique_and_prefixed_by_source() -> None:
    evidence = SplunkStore().loaded_evidence()
    ids = [item.evidence_id for item in evidence]
    assert ids == [
        "EV-AUTH-0001",
        "EV-AUTH-0002",
        "EV-AUTH-0003",
        "EV-PROXY-0001",
        "EV-PROXY-0002",
        "EV-EDR-0001",
        "EV-EDR-0002",
        "EV-EDR-0003",
    ]
    assert len(ids) == len(set(ids))
    for item in evidence:
        spec = REGISTRY[item.source]
        assert item.evidence_id.startswith(spec.id_prefix)
        suffix = item.evidence_id[len(spec.id_prefix) :]
        assert len(suffix) == 4
        assert suffix.isdigit()


def test_alert_ids_referenced_by_evidence_exist() -> None:
    store = SplunkStore()
    known = {alert.alert_id for alert in store.loaded_alerts()}
    assert known == {"SPL-2026-0001"}
    linked = 0
    unlinked = 0
    for item in store.loaded_evidence():
        if not item.alert_ids:
            unlinked += 1
        for alert_id in item.alert_ids:
            assert alert_id in known
            linked += 1
    assert linked == 7
    assert unlinked == 1
    baseline = next(item for item in store.loaded_evidence() if item.evidence_id == "EV-AUTH-0001")
    assert baseline.alert_ids == []


def _copy_fixtures(destination: Path) -> None:
    shutil.copytree(default_fixtures_dir(), destination)


def _read(destination: Path, name: str) -> dict[str, Any]:
    payload: object = json.loads((destination / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write(destination: Path, name: str, payload: dict[str, Any]) -> None:
    (destination / name).write_text(json.dumps(payload), encoding="utf-8")


def _break_content(destination: Path) -> None:
    payload = _read(destination, "auth.json")
    records = payload["records"]
    assert isinstance(records, list)
    record = records[0]
    assert isinstance(record, dict)
    record["content"] = {"result": "success"}
    _write(destination, "auth.json", payload)


def _duplicate_evidence_id(destination: Path) -> None:
    payload = _read(destination, "auth.json")
    records = payload["records"]
    assert isinstance(records, list)
    records.append(records[0])
    _write(destination, "auth.json", payload)


def _unknown_alert_id(destination: Path) -> None:
    payload = _read(destination, "edr.json")
    records = payload["records"]
    assert isinstance(records, list)
    record = records[0]
    assert isinstance(record, dict)
    record["alert_ids"] = ["SPL-DOES-NOT-EXIST"]
    _write(destination, "edr.json", payload)


def _forbid_seq_on_seed_alert(destination: Path) -> None:
    payload = _read(destination, "alerts.json")
    alerts = payload["alerts"]
    assert isinstance(alerts, list)
    alert = alerts[0]
    assert isinstance(alert, dict)
    alert["seq"] = 1
    _write(destination, "alerts.json", payload)


def _invalid_json(destination: Path) -> None:
    (destination / "alerts.json").write_text("{", encoding="utf-8")


def test_loader_rejects_invalid_fixture(tmp_path: Path) -> None:
    mutations = (
        _break_content,
        _duplicate_evidence_id,
        _unknown_alert_id,
        _forbid_seq_on_seed_alert,
        _invalid_json,
    )
    for mutate in mutations:
        destination = tmp_path / mutate.__name__
        _copy_fixtures(destination)
        mutate(destination)
        with pytest.raises(FixtureError):
            SplunkStore(fixtures_dir=destination)
