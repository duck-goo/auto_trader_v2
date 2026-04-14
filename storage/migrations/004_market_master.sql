-- ================================================================
-- 004_market_master: current symbol master snapshot
-- One row per symbol with exclusion flags used by first-stage filter.
-- ================================================================

CREATE TABLE IF NOT EXISTS market_master (
    symbol                  TEXT NOT NULL PRIMARY KEY,
    name                    TEXT NOT NULL,
    market                  TEXT NOT NULL,
    is_managed              INTEGER NOT NULL CHECK (is_managed IN (0, 1)),
    is_investment_warning   INTEGER NOT NULL CHECK (is_investment_warning IN (0, 1)),
    is_investment_risk      INTEGER NOT NULL CHECK (is_investment_risk IN (0, 1)),
    is_attention_issue      INTEGER NOT NULL CHECK (is_attention_issue IN (0, 1)),
    is_disclosure_violation INTEGER NOT NULL CHECK (is_disclosure_violation IN (0, 1)),
    is_liquidation_trade    INTEGER NOT NULL CHECK (is_liquidation_trade IN (0, 1)),
    is_trading_halt         INTEGER NOT NULL CHECK (is_trading_halt IN (0, 1)),
    is_rights_ex_date       INTEGER NOT NULL CHECK (is_rights_ex_date IN (0, 1)),
    is_preferred_stock      INTEGER NOT NULL CHECK (is_preferred_stock IN (0, 1)),
    is_etf                  INTEGER NOT NULL CHECK (is_etf IN (0, 1)),
    is_etn                  INTEGER NOT NULL CHECK (is_etn IN (0, 1)),
    is_spac                 INTEGER NOT NULL CHECK (is_spac IN (0, 1)),
    refreshed_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_master_market
    ON market_master(market);

CREATE INDEX IF NOT EXISTS idx_market_master_refreshed_at
    ON market_master(refreshed_at);
