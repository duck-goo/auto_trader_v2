CREATE TABLE runtime_locks (
    lock_name    TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

CREATE INDEX idx_runtime_locks_expires_at
    ON runtime_locks(expires_at);
