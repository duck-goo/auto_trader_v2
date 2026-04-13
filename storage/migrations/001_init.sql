-- ================================================================
-- 001_init: 초기 스키마 (orders, executions, positions, signals, daily_stats)
-- 모든 시간은 KST ISO8601 문자열. 금액은 INTEGER(원).
-- ================================================================

-- ---------- orders ----------
-- 주문 1건 = 1행. 상태 변화는 UPDATE.
-- client_order_id: 우리가 생성하는 멱등키 (중복 주문 차단의 핵심)
CREATE TABLE IF NOT EXISTS orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id     TEXT NOT NULL UNIQUE,
    kis_order_no        TEXT,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty                 INTEGER NOT NULL CHECK (qty > 0),
    price               INTEGER NOT NULL CHECK (price >= 0),
    order_type          TEXT NOT NULL CHECK (order_type IN ('LIMIT', 'MARKET')),
    status              TEXT NOT NULL CHECK (status IN (
        'PENDING', 'SUBMITTED', 'UNKNOWN',
        'PARTIAL', 'FILLED',
        'CANCELLED', 'REJECTED', 'FAILED'
    )),
    filled_qty          INTEGER NOT NULL DEFAULT 0 CHECK (filled_qty >= 0),
    avg_fill_price      INTEGER NOT NULL DEFAULT 0 CHECK (avg_fill_price >= 0),
    requested_at        TEXT NOT NULL,
    submitted_at        TEXT,
    closed_at           TEXT,
    error_code          TEXT,
    error_message       TEXT,
    strategy_name       TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_status        ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_symbol        ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_kis_order_no  ON orders(kis_order_no);
CREATE INDEX IF NOT EXISTS idx_orders_requested_at  ON orders(requested_at);

-- ---------- executions ----------
-- 체결 이벤트 append-only. 동일 체결 중복 수신 차단.
CREATE TABLE IF NOT EXISTS executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    kis_exec_no     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty             INTEGER NOT NULL CHECK (qty > 0),
    price           INTEGER NOT NULL CHECK (price >= 0),
    executed_at     TEXT NOT NULL,
    UNIQUE (order_id, kis_exec_no),
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_executions_symbol      ON executions(symbol);
CREATE INDEX IF NOT EXISTS idx_executions_executed_at ON executions(executed_at);

-- ---------- positions ----------
-- 종목별 1행. UPSERT로 갱신.
CREATE TABLE IF NOT EXISTS positions (
    symbol      TEXT PRIMARY KEY,
    qty         INTEGER NOT NULL CHECK (qty >= 0),
    avg_price   INTEGER NOT NULL CHECK (avg_price >= 0),
    updated_at  TEXT NOT NULL
);

-- ---------- signals ----------
-- 스캔/시그널 감사 로그.
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    strategy_name   TEXT NOT NULL,
    score           REAL,
    payload_json    TEXT,
    acted           INTEGER NOT NULL DEFAULT 0 CHECK (acted IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_signals_scanned_at ON signals(scanned_at);
CREATE INDEX IF NOT EXISTS idx_signals_symbol     ON signals(symbol);

-- ---------- daily_stats ----------
-- 일별 운영 통계 (리스크 관리용).
CREATE TABLE IF NOT EXISTS daily_stats (
    trade_date      TEXT PRIMARY KEY,           -- YYYY-MM-DD
    realized_pnl    INTEGER NOT NULL DEFAULT 0,
    order_count     INTEGER NOT NULL DEFAULT 0,
    fill_count      INTEGER NOT NULL DEFAULT 0,
    error_count     INTEGER NOT NULL DEFAULT 0
);