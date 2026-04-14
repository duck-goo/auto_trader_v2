-- ================================================================
-- 003_universe_candidates_prev_day_trade_value
--
-- 002 migration used the wrong metric field (`avg_volume_20`).
-- The project spec requires `prev_day_trade_value` in the stored snapshot.
--
-- Existing universe snapshot rows are intentionally discarded here,
-- because their numeric meaning is incompatible with the corrected schema.
-- ================================================================

DROP TABLE IF EXISTS universe_candidates;

CREATE TABLE universe_candidates (
    trade_date            TEXT NOT NULL,   -- YYYY-MM-DD
    symbol                TEXT NOT NULL,
    name                  TEXT NOT NULL,
    market                TEXT NOT NULL,
    close_price           INTEGER NOT NULL CHECK (close_price >= 0),
    prev_day_trade_value  INTEGER NOT NULL CHECK (prev_day_trade_value >= 0),
    refreshed_at          TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);

CREATE INDEX idx_universe_candidates_symbol
    ON universe_candidates(symbol);

CREATE INDEX idx_universe_candidates_refreshed_at
    ON universe_candidates(refreshed_at);
