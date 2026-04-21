-- ================================================================
-- 010_entry_lots
--
-- Track actual filled buy quantities as independent entry lots.
-- A lot is created/expanded only from execution rows, not from order
-- request quantity. This is required for timing2 morning/range entries
-- to be managed separately for stop loss and partial exits.
-- ================================================================

CREATE TABLE entry_lots (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol               TEXT NOT NULL,
    entry_order_id       INTEGER NOT NULL UNIQUE,
    entry_signal_id      INTEGER,
    entry_strategy_name  TEXT NOT NULL,
    entry_slot           TEXT NOT NULL CHECK (entry_slot IN (
        'timing1',
        'timing2_legacy',
        'timing2_morning',
        'timing2_range',
        'manual',
        'unknown'
    )),
    opened_at            TEXT NOT NULL,
    closed_at            TEXT,
    total_buy_qty        INTEGER NOT NULL CHECK (total_buy_qty > 0),
    remaining_qty        INTEGER NOT NULL CHECK (
        remaining_qty >= 0 AND remaining_qty <= total_buy_qty
    ),
    avg_buy_price        INTEGER NOT NULL CHECK (avg_buy_price >= 0),
    realized_sell_qty    INTEGER NOT NULL DEFAULT 0 CHECK (realized_sell_qty >= 0),
    realized_pnl         INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    updated_at           TEXT NOT NULL,
    CHECK (
        (status = 'OPEN' AND remaining_qty > 0 AND closed_at IS NULL)
        OR
        (status = 'CLOSED' AND remaining_qty = 0 AND closed_at IS NOT NULL)
    ),
    FOREIGN KEY (entry_order_id) REFERENCES orders(id) ON DELETE RESTRICT,
    FOREIGN KEY (entry_signal_id) REFERENCES signals(id) ON DELETE SET NULL
);

CREATE INDEX idx_entry_lots_symbol_status
    ON entry_lots(symbol, status, opened_at);

CREATE INDEX idx_entry_lots_entry_slot_status
    ON entry_lots(entry_slot, status, opened_at);

CREATE INDEX idx_entry_lots_entry_signal_id
    ON entry_lots(entry_signal_id);
