-- Original SQLite DDL from researchharness commit ee2148b; retain independently of current migrations.

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS source_runs (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), source_id TEXT NOT NULL,
    config_hash TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
    new_versions INTEGER NOT NULL DEFAULT 0, quarantined INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL REFERENCES source_runs(id),
    source_id TEXT NOT NULL, url TEXT NOT NULL, observed_at TEXT NOT NULL,
    status_code INTEGER, body_hash TEXT, headers_json TEXT NOT NULL,
    context_json TEXT NOT NULL, error TEXT, reused_capture_id TEXT REFERENCES captures(id)
);
CREATE INDEX IF NOT EXISTS captures_url ON captures(source_id, url, observed_at);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, source_id TEXT NOT NULL,
    kind TEXT NOT NULL, record_key TEXT NOT NULL, content_hash TEXT NOT NULL,
    normalizer_version TEXT NOT NULL, data_json TEXT NOT NULL, published_at TEXT,
    UNIQUE(pipeline, source_id, kind, record_key, content_hash, normalizer_version)
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL REFERENCES versions(id),
    source_run_id TEXT NOT NULL REFERENCES source_runs(id), capture_id TEXT NOT NULL REFERENCES captures(id),
    observed_at TEXT NOT NULL, available_at TEXT NOT NULL,
    UNIQUE(version_id, capture_id)
);
CREATE INDEX IF NOT EXISTS observations_asof ON observations(observed_at, available_at);
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_run_id TEXT NOT NULL REFERENCES source_runs(id),
    capture_id TEXT REFERENCES captures(id), location TEXT NOT NULL,
    error TEXT NOT NULL, candidate_text TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_state (
    pipeline TEXT NOT NULL, source_id TEXT NOT NULL, config_hash TEXT NOT NULL,
    last_success_at TEXT NOT NULL, checkpoint_json TEXT NOT NULL,
    PRIMARY KEY(pipeline, source_id)
);
PRAGMA user_version = 1;
