-- ================================================================
-- 005_intraday_bars_15m
--
-- Store completed 15-minute bars captured after the close.
-- This is required for timing1 convergence because 60-period
-- moving averages on 15-minute bars cannot be reconstructed
-- safely from same-day-only KIS minute data.
-- ================================================================

CREATE TABLE intraday_bars_15m (
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

CREATE INDEX idx_intraday_bars_15m_trade_date_symbol
    ON intraday_bars_15m(trade_date, symbol, bar_start_at);

CREATE INDEX idx_intraday_bars_15m_symbol_bar_end_at
    ON intraday_bars_15m(symbol, bar_end_at);
