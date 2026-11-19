"""In-memory Splunk seed: load fixtures, query, reset, and replay.

Lists are ordered by ``(timestamp, evidence_id)`` or by ``seq``. The only
clock read is ``emitted_at`` on a replay.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from services.mock_splunk.stream_engine import StreamEngine

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from contracts.mock_splunk import (
    EVIDENCE_ID_PATTERN,
    Alert,
    AlertPage,
    Entities,
    EvidenceList,
    MitreTechnique,
    ResetResponse,
    SourceList,
    SourceSummary,
    SplunkEvidence,
    format_utc,
)
from contracts.models import APIModel, Severity
from services.mock_splunk import sources as sources_registry
from services.mock_splunk.errors import (
    AlertNotFound,
    FixtureError,
    InvalidWindow,
    UnknownSource,
)
from services.mock_splunk.sources.base import Source

Clock = Callable[[], datetime]
_MAX_WINDOW = timedelta(days=7)
_ID_SUFFIX_LENGTH = 4

ModelT = TypeVar("ModelT", bound=BaseModel)


class _AlertSeed(APIModel):
    alert_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    splunk_severity: Severity
    summary: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)
    mitre_techniques: list[MitreTechnique]
    triggered_at: AwareDatetime
    entities: Entities


class _AlertFile(APIModel):
    seed_version: str = Field(min_length=1)
    alerts: list[_AlertSeed]


class _EvidenceSeed(APIModel):
    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    timestamp: AwareDatetime
    alert_ids: list[str]
    entities: Entities
    content: dict[str, Any]


class _SourceFile(APIModel):
    source: str = Field(min_length=1)
    records: list[_EvidenceSeed]


class _LoadedSeed:
    def __init__(
        self,
        seed_version: str,
        alerts: list[Alert],
        evidence: list[SplunkEvidence],
    ) -> None:
        self.seed_version = seed_version
        self.alerts = alerts
        self.evidence = evidence


def default_fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def _registry() -> Mapping[str, Source]:
    return sources_registry.REGISTRY


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FixtureError(f"missing fixture {path.name}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FixtureError(f"invalid JSON in {path.name}: {exc}") from exc


def _read_model(path: Path, model: type[ModelT]) -> ModelT:
    raw = _read_json(path)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise FixtureError(f"invalid fixture {path.name}: {exc}") from exc


def _id_matches_prefix(evidence_id: str, id_prefix: str) -> bool:
    if id_prefix == "" or not evidence_id.startswith(id_prefix):
        return False
    suffix = evidence_id[len(id_prefix) :]
    return len(suffix) == _ID_SUFFIX_LENGTH and suffix.isdigit()


def _load_alerts(path: Path) -> tuple[str, list[Alert]]:
    document = _read_model(path, _AlertFile)
    alerts: list[Alert] = []
    seen: set[str] = set()
    for index, seed in enumerate(document.alerts, start=1):
        if seed.alert_id in seen:
            raise FixtureError(f"duplicate alert id {seed.alert_id}")
        seen.add(seed.alert_id)
        alerts.append(
            Alert(
                seq=index,
                alert_id=seed.alert_id,
                title=seed.title,
                splunk_severity=seed.splunk_severity,
                summary=seed.summary,
                rule_name=seed.rule_name,
                mitre_techniques=list(seed.mitre_techniques),
                triggered_at=seed.triggered_at,
                emitted_at=seed.triggered_at,
                replay_of=None,
                entities=seed.entities.model_copy(deep=True),
            )
        )
    return document.seed_version, alerts


def _load_evidence(fixtures_dir: Path, known_alerts: set[str]) -> list[SplunkEvidence]:
    evidence: list[SplunkEvidence] = []
    seen_ids: set[str] = set()
    for spec in _registry().values():
        path = fixtures_dir / spec.fixture
        document = _read_model(path, _SourceFile)
        if document.source != spec.name:
            raise FixtureError(
                f"{path.name} declares source {document.source!r}, expected {spec.name!r}",
            )
        for seed in document.records:
            try:
                spec.Record.model_validate(seed.content)
            except ValidationError as exc:
                raise FixtureError(
                    f"invalid {spec.name} content for {seed.evidence_id}: {exc}",
                ) from exc
            if not _id_matches_prefix(seed.evidence_id, spec.id_prefix):
                raise FixtureError(
                    f"{seed.evidence_id} does not use prefix {spec.id_prefix}",
                )
            if seed.evidence_id in seen_ids:
                raise FixtureError(f"duplicate evidence id {seed.evidence_id}")
            seen_ids.add(seed.evidence_id)
            missing = [alert_id for alert_id in seed.alert_ids if alert_id not in known_alerts]
            if missing:
                joined = ", ".join(missing)
                raise FixtureError(
                    f"{seed.evidence_id} references unknown alert ids: {joined}",
                )
            evidence.append(
                SplunkEvidence(
                    evidence_id=seed.evidence_id,
                    source=spec.name,
                    timestamp=seed.timestamp,
                    alert_ids=list(seed.alert_ids),
                    entities=seed.entities.model_copy(deep=True),
                    content=dict(seed.content),
                )
            )
    return evidence


def _load_fixtures(fixtures_dir: Path) -> _LoadedSeed:
    seed_version, alerts = _load_alerts(fixtures_dir / "alerts.json")
    known = {alert.alert_id for alert in alerts}
    evidence = _load_evidence(fixtures_dir, known)
    return _LoadedSeed(seed_version, alerts, evidence)


def _evidence_key(item: SplunkEvidence) -> tuple[datetime, str]:
    return (item.timestamp, item.evidence_id)


def _entity_values(entities: Entities) -> tuple[str, ...]:
    values: list[str] = []
    if entities.user is not None:
        values.append(entities.user)
    if entities.src_ip is not None:
        values.append(entities.src_ip)
    if entities.src_country is not None:
        values.append(entities.src_country)
    values.extend(entities.hosts)
    return tuple(values)


def _entity_matches(entities: Entities, needle: str) -> bool:
    folded = needle.casefold()
    return any(value.casefold() == folded for value in _entity_values(entities))


def _parse_bound(value: str, field: str) -> datetime:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidWindow(
            "window bounds must be ISO-8601 timestamps",
            details={"field": field, "value": value},
        ) from exc
    offset = parsed.tzinfo.utcoffset(parsed) if parsed.tzinfo is not None else None
    if offset is None:
        raise InvalidWindow(
            "window bounds must include a timezone",
            details={"field": field, "value": value},
        )
    return parsed.astimezone(UTC)


def _aware_clock(value: datetime) -> datetime:
    offset = value.tzinfo.utcoffset(value) if value.tzinfo is not None else None
    if offset is None:
        raise RuntimeError("clock must return a timezone-aware datetime")
    return value


class SplunkStore:
    """Fixture-backed alerts and evidence. Replays live only until reset."""

    def __init__(
        self,
        fixtures_dir: Path | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._fixtures_dir = default_fixtures_dir() if fixtures_dir is None else fixtures_dir
        self._clock: Clock = _utc_now if clock is None else clock
        self._seed_version = ""
        self._alerts: dict[str, Alert] = {}
        self._order: list[str] = []
        self._evidence: list[SplunkEvidence] = []
        self._replay_counts: dict[str, int] = {}
        self._next_seq = 1
        self._stream_emitted = 0
        self._stream_next_index = 0
        self._stream_mode: str | None = None
        self._stream_rate_per_min: int | None = None
        self._stream_seed: int | None = None
        self._stream_engine: StreamEngine | None = None
        self.reset()

    @property
    def seed_version(self) -> str:
        return self._seed_version

    def loaded_alerts(self) -> list[Alert]:
        return [self._alerts[alert_id] for alert_id in self._order]

    def loaded_evidence(self) -> list[SplunkEvidence]:
        return list(self._evidence)

    def reset(self) -> ResetResponse:
        """Reload fixtures, drop replays, and restore seeded sequence numbers."""
        if self._stream_engine is not None:
            self._stream_engine.stop()
        loaded = _load_fixtures(self._fixtures_dir)
        self._seed_version = loaded.seed_version
        self._alerts = {alert.alert_id: alert for alert in loaded.alerts}
        self._order = [alert.alert_id for alert in loaded.alerts]
        self._evidence = list(loaded.evidence)
        self._replay_counts = {alert.alert_id: 0 for alert in loaded.alerts}
        self._next_seq = max((alert.seq for alert in loaded.alerts), default=0) + 1
        self._stream_emitted = 0
        self._stream_next_index = 0
        self._stream_mode = None
        self._stream_rate_per_min = None
        self._stream_seed = None
        from services.mock_splunk.stream import reset_registry

        reset_registry()
        return ResetResponse(
            seed_version=self._seed_version,
            alerts=len(self._alerts),
            evidence=len(self._evidence),
        )

    def bind_stream_engine(self, engine: StreamEngine) -> None:
        self._stream_engine = engine

    def stream_start(self, body: Any) -> tuple[bool, dict[str, Any]]:
        if self._stream_engine is None:
            raise RuntimeError("stream engine is not configured")
        from contracts.scenarios import StreamStartRequest

        if isinstance(body, StreamStartRequest):
            request = body
        else:
            request = StreamStartRequest.model_validate(body)
        self._stream_mode = request.mode
        self._stream_rate_per_min = (
            request.rate_per_min
            if request.rate_per_min is not None
            else int(os.environ.get("STREAM_RATE_PER_MIN", "3"))
        )
        self._stream_seed = (
            request.seed if request.seed is not None else int(os.environ.get("STREAM_SEED", "42"))
        )
        started = self._stream_engine.start()
        return started, self.stream_status()

    def stream_stop(self) -> tuple[bool, dict[str, Any]]:
        if self._stream_engine is None:
            raise RuntimeError("stream engine is not configured")
        stopped = self._stream_engine.stop()
        return stopped, self.stream_status()

    def stream_status(self) -> dict[str, Any]:
        rate = self._stream_rate_per_min or int(os.environ.get("STREAM_RATE_PER_MIN", "3"))
        seed = self._stream_seed or int(os.environ.get("STREAM_SEED", "42"))
        running = False if self._stream_engine is None else self._stream_engine.is_running()
        return {
            "running": running,
            "mode": self._stream_mode,
            "rate_per_min": rate,
            "seed": seed,
            "emitted": self._stream_emitted,
            "next_index": self._stream_next_index,
        }

    def emit_stream_tick(self) -> None:
        from services.mock_splunk.stream import generate, list_scenarios, script_schedule

        seed = self._stream_seed or int(os.environ.get("STREAM_SEED", "42"))
        if self._stream_mode == "script":
            schedule = script_schedule(list_scenarios())
            if self._stream_next_index >= len(schedule):
                if self._stream_engine is not None:
                    self._stream_engine.stop()
                return
            scenario = schedule[self._stream_next_index]
            from services.mock_splunk import stream as stream_mod

            batch = [
                stream_mod._emit_one(
                    scenario=scenario,
                    stream_index=self._stream_next_index,
                    clock=self._clock,
                    rng=random.Random(f"{seed}:entity:{self._stream_next_index}"),
                )
            ]
        else:
            batch = generate(seed, 1, start_index=self._stream_next_index, clock=self._clock)
        if not batch:
            return
        item = batch[0]
        self._ingest_stream_item(item)
        self._stream_emitted += 1
        self._stream_next_index += 1

    def _ingest_stream_item(self, item: object) -> None:
        from services.mock_splunk.stream import GeneratedStreamItem

        if not isinstance(item, GeneratedStreamItem):
            raise TypeError("expected GeneratedStreamItem")
        alert = item.alert
        if alert.alert_id in self._alerts:
            return
        seq = self._next_seq
        alert = alert.model_copy(update={"seq": seq})
        self._next_seq += 1
        self._alerts[alert.alert_id] = alert
        self._order.append(alert.alert_id)
        self._evidence.extend(item.evidence)

    def get_alert(self, alert_id: str) -> Alert:
        alert = self._alerts.get(alert_id)
        if alert is None:
            raise AlertNotFound(f"alert {alert_id} not found", details={"alert_id": alert_id})
        return alert

    def list_alerts(
        self,
        *,
        after_seq: int,
        severity: Severity | None,
        limit: int,
    ) -> AlertPage:
        matched = [
            alert
            for alert in self.loaded_alerts()
            if alert.seq > after_seq and (severity is None or alert.splunk_severity == severity)
        ]
        matched.sort(key=lambda alert: alert.seq)
        page = matched[:limit]
        next_after_seq = page[-1].seq if page else after_seq
        return AlertPage(items=page, next_after_seq=next_after_seq)

    def evidence_for_alert(self, alert_id: str, source: str | None) -> EvidenceList:
        alert = self.get_alert(alert_id)
        if source is not None:
            self._require_source(source)
        root = self._root_alert(alert)
        matched = [
            item
            for item in self._evidence
            if root.alert_id in item.alert_ids and (source is None or item.source == source)
        ]
        matched.sort(key=_evidence_key)
        return EvidenceList(items=matched)

    def evidence_by_window(
        self,
        *,
        source: str | None,
        start: str,
        end: str,
        entity: str | None,
    ) -> EvidenceList:
        """Return evidence with ``start <= timestamp < end`` for one source."""
        if source is not None and source != "*":
            self._require_source(source)
        start_at = _parse_bound(start, "start")
        end_at = _parse_bound(end, "end")
        if start_at >= end_at:
            raise InvalidWindow(
                "start must be before end",
                details={"start": format_utc(start_at), "end": format_utc(end_at)},
            )
        if end_at - start_at > _MAX_WINDOW:
            raise InvalidWindow(
                "window must not exceed 7 days",
                details={"start": format_utc(start_at), "end": format_utc(end_at)},
            )
        matched = [
            item
            for item in self._evidence
            if (source is None or source == "*" or item.source == source)
            and start_at <= item.timestamp < end_at
            and (entity is None or _entity_matches(item.entities, entity))
        ]
        matched.sort(key=_evidence_key)
        return EvidenceList(items=matched)

    def list_sources(self) -> SourceList:
        counts: dict[str, int] = {}
        for item in self._evidence:
            counts[item.source] = counts.get(item.source, 0) + 1
        items = [
            SourceSummary(
                name=spec.name,
                id_prefix=spec.id_prefix,
                record_count=counts.get(spec.name, 0),
            )
            for spec in _registry().values()
        ]
        return SourceList(items=items)

    def replay(self, alert_id: str) -> Alert:
        """Append an emission of the root alert. Evidence stays on the root."""
        current = self.get_alert(alert_id)
        root = self._root_alert(current)
        count = self._replay_counts.get(root.alert_id, 0) + 1
        self._replay_counts[root.alert_id] = count
        emitted = Alert(
            seq=self._next_seq,
            alert_id=f"{root.alert_id}-R{count}",
            title=root.title,
            splunk_severity=root.splunk_severity,
            summary=root.summary,
            rule_name=root.rule_name,
            mitre_techniques=list(root.mitre_techniques),
            triggered_at=root.triggered_at,
            emitted_at=_aware_clock(self._clock()),
            replay_of=root.alert_id,
            entities=root.entities.model_copy(deep=True),
        )
        self._next_seq += 1
        self._alerts[emitted.alert_id] = emitted
        self._order.append(emitted.alert_id)
        return emitted

    def _require_source(self, source: str) -> Source:
        spec = _registry().get(source)
        if spec is None:
            raise UnknownSource(f"unknown source {source}", details={"source": source})
        return spec

    def _root_alert(self, alert: Alert) -> Alert:
        seen: set[str] = set()
        current = alert
        while current.replay_of is not None:
            if current.alert_id in seen:
                raise RuntimeError(f"replay cycle at {current.alert_id}")
            seen.add(current.alert_id)
            parent = self._alerts.get(current.replay_of)
            if parent is None:
                raise AlertNotFound(
                    f"replay root {current.replay_of} not found",
                    details={"alert_id": current.replay_of},
                )
            current = parent
        return current
