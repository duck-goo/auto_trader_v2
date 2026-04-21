-- ================================================================
-- 008_intraday_bars_30s
--
-- Store completed 30-second bars used by buy timing 2.
-- Live polling can receive partial windows, so repository writes use
-- upsert semantics instead of replacing an entire symbol/day.
-- ================================================================

CREATE TABLE intraday_bars_30s (
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    bar_start_at  TEXT NOT NULL,
    bar_end_at    TEXT NOT NULL,
    open          INTEGER NOT NULL CHECK (open >= 0),
    high          INTEGER NOT NULL CHECK (high >= 0),
    low           INTEGER NOT NULL CHECK (low >= 0),
    close         INTEGER NOT NULL CHECK (close >= 0),
    volume        INTEGER NOT NULL CHECK (volume >= 0),
    refreshed_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, bar_start_at)
);

CREATE INDEX idx_intraday_bars_30s_trade_date_symbol
    ON intraday_bars_30s(trade_date, symbol, bar_start_at);

CREATE INDEX idx_intraday_bars_30s_symbol_bar_end_at
    ON intraday_bars_30s(symbol, bar_end_at);

CREATE INDEX idx_intraday_bars_30s_trade_date_symbol_bar_end_at
    ON intraday_bars_30s(trade_date, symbol, bar_end_at);
