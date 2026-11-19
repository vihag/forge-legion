CREATE TABLE IF NOT EXISTS soar_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    soar_case_id TEXT NOT NULL UNIQUE,
    case_id INTEGER NOT NULL UNIQUE,
    alert_id TEXT NOT NULL,
    disposition_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    approval_json TEXT NOT NULL,
    submitted_by_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_soar_submissions_case_id ON soar_submissions(case_id);