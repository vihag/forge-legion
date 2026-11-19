-- Case store schema. Applied on startup with CREATE TABLE IF NOT EXISTS.
-- `python -m services.case_api.reset` drops these tables and applies this file again.

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY,
    alert_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT,
    splunk_severity TEXT NOT NULL,
    triage_severity TEXT,
    triage_rationale TEXT,
    triage_model TEXT,
    assigned_analyst TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    source TEXT NOT NULL,
    ref TEXT NOT NULL,
    content_json TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    kind TEXT NOT NULL CHECK (kind IN ('observation', 'hypothesis')),
    statement TEXT NOT NULL,
    confidence REAL,
    author_id TEXT NOT NULL,
    author_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_evidence (
    finding_id INTEGER NOT NULL REFERENCES findings(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (finding_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS dispositions (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    action TEXT NOT NULL,
    rationale TEXT NOT NULL,
    finding_ids_json TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    disposition_id INTEGER NOT NULL REFERENCES dispositions(id),
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    analyst_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS soar_submissions (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL UNIQUE REFERENCES cases(id),
    disposition_id INTEGER NOT NULL REFERENCES dispositions(id),
    soar_case_id TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER REFERENCES cases(id),
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    component TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    payload_json TEXT NOT NULL
);
