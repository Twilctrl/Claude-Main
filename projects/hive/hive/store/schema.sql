-- Hive's database. The events table is the authoritative log of every run;
-- the other run tables hold current state for fast queries (DESIGN.md §11).
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    workspace TEXT NOT NULL,
    created_at REAL NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    config TEXT NOT NULL,          -- team and limits, as started
    started_at REAL NOT NULL,
    ended_at REAL,
    stats TEXT NOT NULL DEFAULT '{}',
    summary TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    spec TEXT NOT NULL,            -- AgentSpec as JSON (prompt edits update it)
    status TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '{}',  -- history, summary, counters
    PRIMARY KEY (run_id, name)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    agent TEXT,
    payload TEXT NOT NULL,
    rolled_back INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS events_run_seq ON events(run_id, seq);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,       -- agent name, '#channel', or 'user'
    content TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_run ON messages(run_id, seq);

CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id INTEGER NOT NULL,
    data TEXT NOT NULL,            -- Task as JSON
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS board (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id INTEGER NOT NULL,
    data TEXT NOT NULL,            -- BoardEntry as JSON
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,         -- sha256 of the uncompressed content
    data BLOB NOT NULL             -- zlib
);

CREATE TABLE IF NOT EXISTS file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT,
    path TEXT NOT NULL,
    version INTEGER NOT NULL,
    agent TEXT NOT NULL,           -- or 'external' for edits made outside Hive
    seq INTEGER,
    ts REAL NOT NULL,
    hash TEXT,                     -- NULL when deleted
    size INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS file_versions_path ON file_versions(project_id, path, version);

CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms REAL,
    ok INTEGER NOT NULL,
    error TEXT,
    request TEXT,
    response TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    status TEXT NOT NULL,          -- pending | approved | rejected
    decided_by TEXT,
    reason TEXT,
    created_at REAL NOT NULL,
    decided_at REAL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    label TEXT NOT NULL,
    state TEXT NOT NULL
);
