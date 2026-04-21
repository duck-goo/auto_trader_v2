-- ================================================================
-- 009_current_price_samples
--
-- Persist raw current-price snapshots captured during intraday polling.
-- These samples are the auditable source for later 30-second bar building.
-- ================================================================

CREATE TABLE current_price_samples (
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    price         INTEGER NOT NULL CHECK (price >= 0),
    open          INTEGER NOT NULL CHECK (open >= 0),
    high          INTEGER NOT NULL CHECK (high >= 0),
    low           INTEGER NOT NULL CHECK (low >= 0),
    prev_close    INTEGER NOT NULL CHECK (prev_close >= 0),
    change        INTEGER NOT NULL,
    change_rate   REAL NOT NULL,
    volume        INTEGER NOT NULL CHECK (volume >= 0),
    source        TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    PRIMARY KEY (symbol, observed_at)
);

CREATE INDEX idx_current_price_samples_trade_date_symbol
    ON current_price_samples(trade_date, symbol, observed_at);

CREATE INDEX idx_current_price_samples_symbol_observed_at
    ON current_price_samples(symbol, observed_at);
