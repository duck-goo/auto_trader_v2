-- ================================================================
-- 002_universe_candidates: 1차 필터 결과 snapshot
-- trade_date 1일 기준으로 symbol별 후보 목록을 저장한다.
-- ================================================================

CREATE TABLE IF NOT EXISTS universe_candidates (
    trade_date      TEXT NOT NULL,          -- YYYY-MM-DD
    symbol          TEXT NOT NULL,
    name            TEXT NOT NULL,
    market          TEXT NOT NULL,
    close_price     INTEGER NOT NULL CHECK (close_price >= 0),
    prev_day_trade_value   INTEGER NOT NULL CHECK (prev_day_trade_value >= 0),
    refreshed_at    TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_universe_candidates_symbol
    ON universe_candidates(symbol);

CREATE INDEX IF NOT EXISTS idx_universe_candidates_refreshed_at
    ON universe_candidates(refreshed_at);
