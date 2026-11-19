"""SQLite repository for the case store.

Connections use autocommit (`isolation_level=None`) so each mutating call can own
one explicit transaction. A process-wide lock serializes use of the single connection.
"""

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from contracts.models import (
    Actor,
    ActorType,
    AuditEvent,
    Case,
    CaseStatus,
    CaseSummary,
    Decision,
    DecisionValue,
    Disposition,
    DispositionAction,
    Evidence,
    Finding,
    FindingKind,
    Severity,
    SoarSubmission,
)

_DROP_ORDER = (
    "finding_evidence",
    "decisions",
    "soar_submissions",
    "dispositions",
    "findings",
    "evidence",
    "audit_events",
    "cases",
)


def _schema_sql() -> str:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "db" / "schema.sql"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError("db/schema.sql")


_SCHEMA_SQL = _schema_sql()


def _last_id(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:
        raise RuntimeError("insert did not return a row id")
    return int(row_id)


def _cell_int(row: sqlite3.Row, key: str) -> int:
    value: object = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"column {key} is not int")
    return value


def _cell_str(row: sqlite3.Row, key: str) -> str:
    value: object = row[key]
    if not isinstance(value, str):
        raise TypeError(f"column {key} is not str")
    return value


def _cell_optional_str(row: sqlite3.Row, key: str) -> str | None:
    value: object = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"column {key} is not str")
    return value


def _cell_optional_int(row: sqlite3.Row, key: str) -> int | None:
    value: object = row[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"column {key} is not int")
    return value


def _cell_optional_float(row: sqlite3.Row, key: str) -> float | None:
    value: object = row[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"column {key} is not float")
    return float(value)


def _json_object(raw: str) -> dict[str, Any]:
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("expected a JSON object")
    result: dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            raise TypeError("expected string keys")
        result[key] = value
    return result


def _json_int_list(raw: str) -> list[int]:
    parsed: object = json.loads(raw)
    if not isinstance(parsed, list):
        raise TypeError("expected a JSON array")
    result: list[int] = []
    for item in parsed:
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError("expected an int list")
        result.append(item)
    return result


def _actor_type(value: str) -> ActorType:
    if value == "analyst":
        return "analyst"
    if value == "agent":
        return "agent"
    if value == "system":
        return "system"
    raise ValueError(f"invalid actor type {value}")


def _severity(value: str) -> Severity:
    if value == "low":
        return "low"
    if value == "medium":
        return "medium"
    if value == "high":
        return "high"
    if value == "critical":
        return "critical"
    raise ValueError(f"invalid severity {value}")


def _optional_severity(value: str | None) -> Severity | None:
    if value is None:
        return None
    return _severity(value)


def _status(value: str) -> CaseStatus:
    try:
        return CaseStatus(value)
    except ValueError as exc:
        raise ValueError(f"invalid status {value}") from exc


def _optional_status(value: str | None) -> CaseStatus | None:
    if value is None:
        return None
    return _status(value)


def _finding_kind(value: str) -> FindingKind:
    if value == "observation":
        return "observation"
    if value == "hypothesis":
        return "hypothesis"
    raise ValueError(f"invalid finding kind {value}")


def _action(value: str) -> DispositionAction:
    if value == "false_positive":
        return "false_positive"
    if value == "benign":
        return "benign"
    if value == "escalate":
        return "escalate"
    if value == "contain":
        return "contain"
    raise ValueError(f"invalid disposition action {value}")


def _decision_value(value: str) -> DecisionValue:
    if value == "approve":
        return "approve"
    if value == "reject":
        return "reject"
    raise ValueError(f"invalid decision {value}")


def _case_from_row(row: sqlite3.Row) -> Case:
    return Case(
        id=_cell_int(row, "id"),
        alert_id=_cell_str(row, "alert_id"),
        title=_cell_str(row, "title"),
        summary=_cell_optional_str(row, "summary"),
        splunk_severity=_severity(_cell_str(row, "splunk_severity")),
        triage_severity=_optional_severity(_cell_optional_str(row, "triage_severity")),
        triage_rationale=_cell_optional_str(row, "triage_rationale"),
        triage_model=_cell_optional_str(row, "triage_model"),
        assigned_analyst=_cell_optional_str(row, "assigned_analyst"),
        status=_status(_cell_str(row, "status")),
        created_at=_cell_str(row, "created_at"),
        updated_at=_cell_str(row, "updated_at"),
    )


def _summary_from_row(row: sqlite3.Row) -> CaseSummary:
    splunk = _severity(_cell_str(row, "splunk_severity"))
    triage = _optional_severity(_cell_optional_str(row, "triage_severity"))
    return CaseSummary(
        id=_cell_int(row, "id"),
        alert_id=_cell_str(row, "alert_id"),
        title=_cell_str(row, "title"),
        severity=triage if triage is not None else splunk,
        splunk_severity=splunk,
        status=_status(_cell_str(row, "status")),
        assigned_analyst=_cell_optional_str(row, "assigned_analyst"),
        created_at=_cell_str(row, "created_at"),
        updated_at=_cell_str(row, "updated_at"),
    )


def _evidence_from_row(row: sqlite3.Row) -> Evidence:
    return Evidence(
        id=_cell_int(row, "id"),
        case_id=_cell_int(row, "case_id"),
        source=_cell_str(row, "source"),
        ref=_cell_str(row, "ref"),
        content=_json_object(_cell_str(row, "content_json")),
        added_by=_cell_str(row, "added_by"),
        created_at=_cell_str(row, "created_at"),
    )


def _finding_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Finding:
    finding_id = _cell_int(row, "id")
    links = conn.execute(
        "SELECT evidence_id FROM finding_evidence WHERE finding_id = ? ORDER BY rowid",
        (finding_id,),
    ).fetchall()
    return Finding(
        id=finding_id,
        case_id=_cell_int(row, "case_id"),
        kind=_finding_kind(_cell_str(row, "kind")),
        statement=_cell_str(row, "statement"),
        evidence_ids=[_cell_int(link, "evidence_id") for link in links],
        confidence=_cell_optional_float(row, "confidence"),
        author_id=_cell_str(row, "author_id"),
        author_type=_actor_type(_cell_str(row, "author_type")),
        created_at=_cell_str(row, "created_at"),
    )


def _disposition_from_row(row: sqlite3.Row) -> Disposition:
    return Disposition(
        id=_cell_int(row, "id"),
        case_id=_cell_int(row, "case_id"),
        action=_action(_cell_str(row, "action")),
        rationale=_cell_str(row, "rationale"),
        finding_ids=_json_int_list(_cell_str(row, "finding_ids_json")),
        proposed_by=_cell_str(row, "proposed_by"),
        created_at=_cell_str(row, "created_at"),
        superseded=bool(_cell_int(row, "superseded")),
    )


def _decision_from_row(row: sqlite3.Row) -> Decision:
    return Decision(
        id=_cell_int(row, "id"),
        case_id=_cell_int(row, "case_id"),
        disposition_id=_cell_int(row, "disposition_id"),
        decision=_decision_value(_cell_str(row, "decision")),
        rationale=_cell_str(row, "rationale"),
        analyst_id=_cell_str(row, "analyst_id"),
        created_at=_cell_str(row, "created_at"),
    )


def _soar_from_row(row: sqlite3.Row) -> SoarSubmission:
    return SoarSubmission(
        id=_cell_int(row, "id"),
        case_id=_cell_int(row, "case_id"),
        disposition_id=_cell_int(row, "disposition_id"),
        soar_case_id=_cell_str(row, "soar_case_id"),
        submitted_by=_cell_str(row, "submitted_by"),
        created_at=_cell_str(row, "created_at"),
    )


def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=_cell_int(row, "id"),
        case_id=_cell_optional_int(row, "case_id"),
        ts=_cell_str(row, "ts"),
        type=_cell_str(row, "type"),
        actor=Actor(
            id=_cell_str(row, "actor_id"),
            type=_actor_type(_cell_str(row, "actor_type")),
        ),
        component=_cell_str(row, "component"),
        from_status=_optional_status(_cell_optional_str(row, "from_status")),
        to_status=_optional_status(_cell_optional_str(row, "to_status")),
        payload=_json_object(_cell_str(row, "payload_json")),
    )


class Store:
    """One SQLite connection and the statements that read and write it."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None keeps Python from opening its own transactions.
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_SCHEMA_SQL)
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def reset(self) -> None:
        """Drop every table and apply the schema again."""
        with self._lock:
            if self._conn.in_transaction:
                self._conn.rollback()
            self._conn.execute("PRAGMA foreign_keys = OFF")
            try:
                for table in _DROP_ORDER:
                    self._conn.execute(f"DROP TABLE IF EXISTS {table}")
                self._conn.executescript(_SCHEMA_SQL)
            finally:
                self._conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One immediate transaction. Commit on success, roll back on any error."""
        with self._lock:
            if self._conn.in_transaction:
                raise RuntimeError("transaction already open")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def insert_case(
        self,
        conn: sqlite3.Connection,
        *,
        alert_id: str,
        title: str,
        summary: str | None,
        splunk_severity: str,
        assigned_analyst: str | None,
        status: str,
        created_at: str,
        updated_at: str,
    ) -> Case:
        cursor = conn.execute(
            """
            INSERT INTO cases (
                alert_id, title, summary, splunk_severity, assigned_analyst,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                title,
                summary,
                splunk_severity,
                assigned_analyst,
                status,
                created_at,
                updated_at,
            ),
        )
        return self._must_case(conn, _last_id(cursor))

    def get_case(self, conn: sqlite3.Connection, case_id: int) -> Case | None:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        return _case_from_row(row)

    def get_case_by_alert_id(self, conn: sqlite3.Connection, alert_id: str) -> Case | None:
        row = conn.execute("SELECT * FROM cases WHERE alert_id = ?", (alert_id,)).fetchone()
        if row is None:
            return None
        return _case_from_row(row)

    def list_cases(
        self,
        conn: sqlite3.Connection,
        *,
        status: str | None,
        severity: str | None,
        alert_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[CaseSummary], int]:
        clauses = ["1 = 1"]
        params: list[str | int] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if severity is not None:
            clauses.append("COALESCE(triage_severity, splunk_severity) = ?")
            params.append(severity)
        if alert_id is not None:
            clauses.append("alert_id = ?")
            params.append(alert_id)
        # Fragments above are fixed SQL. Filter values are bound parameters.
        where = " AND ".join(clauses)
        count_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM cases WHERE {where}",
            params,
        ).fetchone()
        total = 0 if count_row is None else _cell_int(count_row, "n")
        rows = conn.execute(
            f"SELECT * FROM cases WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [_summary_from_row(row) for row in rows], total

    def update_triage(
        self,
        conn: sqlite3.Connection,
        case_id: int,
        *,
        severity: str,
        rationale: str,
        model: str,
        status: str,
        updated_at: str,
    ) -> Case:
        conn.execute(
            """
            UPDATE cases
               SET triage_severity = ?, triage_rationale = ?, triage_model = ?,
                   status = ?, updated_at = ?
             WHERE id = ?
            """,
            (severity, rationale, model, status, updated_at, case_id),
        )
        return self._must_case(conn, case_id)

    def update_case_status(
        self,
        conn: sqlite3.Connection,
        case_id: int,
        status: str,
        updated_at: str,
    ) -> Case:
        conn.execute(
            "UPDATE cases SET status = ?, updated_at = ? WHERE id = ?",
            (status, updated_at, case_id),
        )
        return self._must_case(conn, case_id)

    def insert_evidence(
        self,
        conn: sqlite3.Connection,
        *,
        case_id: int,
        source: str,
        ref: str,
        content: Mapping[str, Any],
        added_by: str,
        created_at: str,
    ) -> Evidence:
        cursor = conn.execute(
            """
            INSERT INTO evidence (case_id, source, ref, content_json, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (case_id, source, ref, json.dumps(dict(content)), added_by, created_at),
        )
        row = conn.execute(
            "SELECT * FROM evidence WHERE id = ?",
            (_last_id(cursor),),
        ).fetchone()
        if row is None:
            raise RuntimeError("inserted evidence missing")
        return _evidence_from_row(row)

    def list_evidence(self, conn: sqlite3.Connection, case_id: int) -> list[Evidence]:
        rows = conn.execute(
            "SELECT * FROM evidence WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def evidence_ids(self, conn: sqlite3.Connection, case_id: int) -> set[int]:
        rows = conn.execute("SELECT id FROM evidence WHERE case_id = ?", (case_id,)).fetchall()
        return {_cell_int(row, "id") for row in rows}

    def insert_finding(
        self,
        conn: sqlite3.Connection,
        *,
        case_id: int,
        kind: str,
        statement: str,
        confidence: float | None,
        author: Actor,
        evidence_ids: list[int],
        created_at: str,
    ) -> Finding:
        cursor = conn.execute(
            """
            INSERT INTO findings (
                case_id, kind, statement, confidence, author_id, author_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (case_id, kind, statement, confidence, author.id, author.type, created_at),
        )
        finding_id = _last_id(cursor)
        for evidence_id in evidence_ids:
            conn.execute(
                "INSERT INTO finding_evidence (finding_id, evidence_id) VALUES (?, ?)",
                (finding_id, evidence_id),
            )
        row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        if row is None:
            raise RuntimeError("inserted finding missing")
        return _finding_from_row(conn, row)

    def list_findings(self, conn: sqlite3.Connection, case_id: int) -> list[Finding]:
        rows = conn.execute(
            "SELECT * FROM findings WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
        return [_finding_from_row(conn, row) for row in rows]

    def supersede_open_dispositions(self, conn: sqlite3.Connection, case_id: int) -> None:
        conn.execute(
            "UPDATE dispositions SET superseded = 1 WHERE case_id = ? AND superseded = 0",
            (case_id,),
        )

    def insert_disposition(
        self,
        conn: sqlite3.Connection,
        *,
        case_id: int,
        action: str,
        rationale: str,
        finding_ids: list[int],
        proposed_by: str,
        created_at: str,
    ) -> Disposition:
        cursor = conn.execute(
            """
            INSERT INTO dispositions (
                case_id, action, rationale, finding_ids_json, proposed_by, created_at, superseded
            ) VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (case_id, action, rationale, json.dumps(finding_ids), proposed_by, created_at),
        )
        row = conn.execute(
            "SELECT * FROM dispositions WHERE id = ?",
            (_last_id(cursor),),
        ).fetchone()
        if row is None:
            raise RuntimeError("inserted disposition missing")
        return _disposition_from_row(row)

    def get_open_disposition(self, conn: sqlite3.Connection, case_id: int) -> Disposition | None:
        row = conn.execute(
            """
            SELECT * FROM dispositions
             WHERE case_id = ? AND superseded = 0
             ORDER BY id DESC
             LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        return _disposition_from_row(row)

    def insert_decision(
        self,
        conn: sqlite3.Connection,
        *,
        case_id: int,
        disposition_id: int,
        decision: str,
        rationale: str,
        analyst_id: str,
        created_at: str,
    ) -> Decision:
        cursor = conn.execute(
            """
            INSERT INTO decisions (
                case_id, disposition_id, decision, rationale, analyst_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (case_id, disposition_id, decision, rationale, analyst_id, created_at),
        )
        row = conn.execute(
            "SELECT * FROM decisions WHERE id = ?",
            (_last_id(cursor),),
        ).fetchone()
        if row is None:
            raise RuntimeError("inserted decision missing")
        return _decision_from_row(row)

    def get_decision_for_disposition(
        self,
        conn: sqlite3.Connection,
        disposition_id: int,
    ) -> Decision | None:
        row = conn.execute(
            "SELECT * FROM decisions WHERE disposition_id = ? ORDER BY id DESC LIMIT 1",
            (disposition_id,),
        ).fetchone()
        if row is None:
            return None
        return _decision_from_row(row)

    def insert_soar(
        self,
        conn: sqlite3.Connection,
        *,
        case_id: int,
        disposition_id: int,
        soar_case_id: str,
        submitted_by: str,
        created_at: str,
    ) -> SoarSubmission:
        cursor = conn.execute(
            """
            INSERT INTO soar_submissions (
                case_id, disposition_id, soar_case_id, submitted_by, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (case_id, disposition_id, soar_case_id, submitted_by, created_at),
        )
        row = conn.execute(
            "SELECT * FROM soar_submissions WHERE id = ?",
            (_last_id(cursor),),
        ).fetchone()
        if row is None:
            raise RuntimeError("inserted SOAR submission missing")
        return _soar_from_row(row)

    def get_soar(self, conn: sqlite3.Connection, case_id: int) -> SoarSubmission | None:
        row = conn.execute(
            "SELECT * FROM soar_submissions WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        return _soar_from_row(row)

    def insert_audit(
        self,
        conn: sqlite3.Connection,
        *,
        case_id: int | None,
        ts: str,
        event_type: str,
        actor: Actor,
        component: str,
        from_status: str | None,
        to_status: str | None,
        payload: Mapping[str, Any],
    ) -> AuditEvent:
        cursor = conn.execute(
            """
            INSERT INTO audit_events (
                case_id, ts, type, actor_id, actor_type, component,
                from_status, to_status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                ts,
                event_type,
                actor.id,
                actor.type,
                component,
                from_status,
                to_status,
                json.dumps(dict(payload)),
            ),
        )
        row = conn.execute(
            "SELECT * FROM audit_events WHERE id = ?",
            (_last_id(cursor),),
        ).fetchone()
        if row is None:
            raise RuntimeError("inserted audit event missing")
        return _audit_from_row(row)

    def list_history(self, conn: sqlite3.Connection, case_id: int) -> list[AuditEvent]:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
        return [_audit_from_row(row) for row in rows]

    def list_events(
        self,
        conn: sqlite3.Connection,
        *,
        after_id: int,
        case_id: int | None,
        limit: int,
    ) -> list[AuditEvent]:
        if case_id is None:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE id > ? ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM audit_events
                 WHERE id > ? AND case_id = ?
                 ORDER BY id
                 LIMIT ?
                """,
                (after_id, case_id, limit),
            ).fetchall()
        return [_audit_from_row(row) for row in rows]

    def _must_case(self, conn: sqlite3.Connection, case_id: int) -> Case:
        case = self.get_case(conn, case_id)
        if case is None:
            raise RuntimeError(f"case {case_id} is missing")
        return case
