"""SQLite store for Mock SOAR submissions."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Self

from contracts.mock_soar import SoarApproval, SoarRecord, SoarSubmissionRequest
from contracts.models import Actor

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "mock_soar.sql"


def _schema_sql() -> str:
    if not _SCHEMA_PATH.is_file():
        raise FileNotFoundError("db/mock_soar.sql")
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def _soar_id(row_id: int) -> str:
    return f"SOAR-{row_id:06d}"


def _actor_json(actor: Actor) -> str:
    return json.dumps(actor.model_dump(mode="json"))


def _actor_from_json(raw: str) -> Actor:
    return Actor.model_validate_json(raw)


def _approval_json(approval: SoarApproval) -> str:
    return json.dumps(approval.model_dump(mode="json"))


def _approval_from_json(raw: str) -> SoarApproval:
    return SoarApproval.model_validate_json(raw)


class SoarStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_schema_sql())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def reset(self) -> int:
        with self._lock:
            self._conn.execute("DELETE FROM soar_submissions")
            self._conn.execute("DELETE FROM sqlite_sequence WHERE name = 'soar_submissions'")
            return 0

    def count(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) AS n FROM soar_submissions").fetchone()
        if row is None:
            return 0
        value: object = row["n"]
        return int(value) if isinstance(value, int) else 0

    def get_by_soar_id(self, conn: sqlite3.Connection, soar_case_id: str) -> SoarRecord | None:
        row = conn.execute(
            "SELECT * FROM soar_submissions WHERE soar_case_id = ?",
            (soar_case_id,),
        ).fetchone()
        if row is None:
            return None
        return _record_from_row(row)

    def get_by_case_id(self, conn: sqlite3.Connection, case_id: int) -> SoarRecord | None:
        row = conn.execute(
            "SELECT * FROM soar_submissions WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        return _record_from_row(row)

    def list_by_case_id(self, conn: sqlite3.Connection, case_id: int) -> list[SoarRecord]:
        rows = conn.execute(
            "SELECT * FROM soar_submissions WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
        return [_record_from_row(row) for row in rows]


def _record_from_row(row: sqlite3.Row) -> SoarRecord:
    return SoarRecord(
        soar_case_id=str(row["soar_case_id"]),
        case_id=int(row["case_id"]),
        alert_id=str(row["alert_id"]),
        disposition_id=int(row["disposition_id"]),
        action=row["action"],
        approval=_approval_from_json(str(row["approval_json"])),
        submitted_by=_actor_from_json(str(row["submitted_by_json"])),
        recorded_at=str(row["recorded_at"]),
    )


def insert_submission(
    conn: sqlite3.Connection,
    body: SoarSubmissionRequest,
    *,
    recorded_at: str,
) -> tuple[SoarRecord, bool]:
    placeholder = f"PENDING-case-{body.case_id}"
    cursor = conn.execute(
        """
        INSERT INTO soar_submissions (
            soar_case_id, case_id, alert_id, disposition_id, action,
            approval_json, submitted_by_json, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            placeholder,
            body.case_id,
            body.alert_id,
            body.disposition_id,
            body.action,
            _approval_json(body.approval),
            _actor_json(body.submitted_by),
            recorded_at,
        ),
    )
    row_id = cursor.lastrowid
    if row_id is None:
        raise RuntimeError("insert did not return row id")
    soar_case_id = _soar_id(int(row_id))
    conn.execute(
        "UPDATE soar_submissions SET soar_case_id = ? WHERE id = ?",
        (soar_case_id, row_id),
    )
    row = conn.execute("SELECT * FROM soar_submissions WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise RuntimeError("inserted submission missing")
    return _record_from_row(row), True