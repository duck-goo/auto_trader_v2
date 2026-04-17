CREATE TABLE IF NOT EXISTS trading_controls (
    control_name    TEXT PRIMARY KEY,
    is_enabled      INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
    note            TEXT,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trading_controls_updated_at
    ON trading_controls(updated_at);
