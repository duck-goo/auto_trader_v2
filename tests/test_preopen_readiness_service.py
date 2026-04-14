"""Tests for PreopenReadinessService."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, Holding, OrderInfo, PriceSnapshot
from services import (
    MarketMasterRefreshItem,
    PreopenReadinessOutcome,
    PreopenReadinessResult,
    PreopenReadinessService,
    UniverseFilterSettings,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    MarketMasterRepository,
    OrderRepository,
    PositionRepository,
    UniverseCandidate,
    UniverseCandidateRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-14"


class FakeBroker(BrokerInterface):
    def __init__(
        self,
        *,
        candle_map: dict[str, pd.DataFrame],
        balance: Balance,
    ) -> None:
        self._candle_map = candle_map
        self._balance = balance

    def get_access_token(self) -> str:
        raise NotImplementedError

    def get_current_price(self, code: str) -> PriceSnapshot:
        raise NotImplementedError

    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self._candle_map[code]

    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> pd.DataFrame:
        raise NotImplementedError

    def get_balance(self) -> Balance:
        return self._balance

    def place_order(
        self,
        code: str,
        side: str,
        quantity: int,
        price: int = 0,
    ) -> OrderInfo:
        raise NotImplementedError

    def cancel_order(
        self,
        order_no: str,
        code: str,
        quantity: int,
    ) -> OrderInfo:
        raise NotImplementedError

    def get_order_status(
        self,
        order_no: str | None = None,
        *,
        filled_only: bool = False,
    ) -> list[OrderInfo]:
        raise NotImplementedError


def _fixed_now():
    fixed = KST.localize(datetime(2026, 4, 14, 8, 30, 0))
    return lambda: fixed


def _daily_df(*, close_price: int, trade_value: int) -> pd.DataFrame:
    rows = []
    for day in range(1, 31):
        rows.append(
            {
                "datetime": KST.localize(datetime(2026, 3, day, 0, 0, 0)),
                "open": close_price - 10,
                "high": close_price + 10,
                "low": close_price - 20,
                "close": close_price,
                "volume": 1000 + day,
                "trade_value": trade_value,
            }
        )
    rows.append(
        {
            "datetime": KST.localize(datetime(2026, 4, 14, 0, 0, 0)),
            "open": 9999,
            "high": 9999,
            "low": 9999,
            "close": 9999,
            "volume": 9999,
            "trade_value": 999_999_999,
        }
    )
    return pd.DataFrame(rows)


def _settings() -> UniverseFilterSettings:
    return UniverseFilterSettings(
        min_price=5_000,
        max_price=200_000,
        min_avg_trade_value_20=100_000_000,
    )


def _master_items() -> list[MarketMasterRefreshItem]:
    return [
        MarketMasterRefreshItem(
            symbol="035420",
            name="NAVER",
            market="KOSPI",
        ),
        MarketMasterRefreshItem(
            symbol="069500",
            name="KODEX 200",
            market="ETF",
            is_etf=True,
        ),
    ]


def _make_balance(*holdings: Holding) -> Balance:
    return Balance(
        cash=1_000_000,
        available_cash=1_000_000,
        total_eval=0,
        total_profit=0,
        holdings=tuple(holdings),
        has_more_pages=False,
        timestamp=KST.localize(datetime(2026, 4, 14, 10, 30, 0)),
    )


def _holding(*, code: str, qty: int, avg_price: float) -> Holding:
    return Holding(
        code=code,
        name=code,
        quantity=qty,
        available=qty,
        avg_price=avg_price,
        current_price=0,
        eval_amount=0,
        profit=0,
        profit_rate=0.0,
    )


def _seed_existing_universe(conn, repo):
    with transaction(conn):
        repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=950_000_000_000,
                )
            ],
            refreshed_at="2026-04-14T08:00:00+09:00",
        )


def _seed_unresolved_order(conn, order_repo):
    with transaction(conn):
        order_repo.create(
            client_order_id="COID_PREOPEN_BLOCK",
            symbol="035420",
            side="buy",
            qty=1,
            price=180000,
            order_type="LIMIT",
            strategy_name="preopen",
            requested_at="2026-04-14T09:00:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id="COID_PREOPEN_BLOCK",
            kis_order_no="KIS_PREOPEN_001",
            submitted_at="2026-04-14T09:00:01+09:00",
        )


def _make_conn(test_db_path):
    run_migrations(test_db_path)
    return get_connection(test_db_path)


def _make_service(conn) -> PreopenReadinessService:
    return PreopenReadinessService(
        conn=conn,
        market_master_repo=MarketMasterRepository(conn),
        universe_repo=UniverseCandidateRepository(conn),
        order_repo=OrderRepository(conn),
        position_repo=PositionRepository(conn),
        now_fn=_fixed_now(),
    )


def _make_broker(*, trade_value_for_naver: int = 410_000_000_000) -> FakeBroker:
    return FakeBroker(
        candle_map={
            "035420": _daily_df(
                close_price=180000,
                trade_value=trade_value_for_naver,
            ),
            "069500": _daily_df(
                close_price=36250,
                trade_value=120_000_000_000,
            ),
        },
        balance=_make_balance(
            _holding(code="035420", qty=1, avg_price=180000.0)
        ),
    )


def test_prepare_and_check_returns_prepared_only_when_startup_not_requested(
    test_db_path,
):
    conn = _make_conn(test_db_path)
    try:
        result = _make_service(conn).prepare_and_check(
            broker=_make_broker(),
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=False,
            run_startup_check=False,
        )

        assert isinstance(result, PreopenReadinessResult)
        assert result.outcome == PreopenReadinessOutcome.PREPARED_ONLY
        assert result.startup_check_result is None
        assert result.preopen_universe_result.universe_build_result.outcome.value == "DRY_RUN"
    finally:
        conn.close()


def test_prepare_and_check_skips_startup_when_universe_not_saved(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        result = _make_service(conn).prepare_and_check(
            broker=_make_broker(),
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=False,
            run_startup_check=True,
        )

        assert result.outcome == PreopenReadinessOutcome.STARTUP_SKIPPED
        assert result.startup_check_result is None
        assert result.reason == (
            "Startup check skipped because universe snapshot was not saved. "
            "build_outcome=DRY_RUN"
        )
    finally:
        conn.close()


def test_prepare_and_check_ready_after_saved_universe(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        result = _make_service(conn).prepare_and_check(
            broker=_make_broker(),
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=True,
            run_startup_check=True,
        )

        assert result.outcome == PreopenReadinessOutcome.READY
        assert result.startup_check_result is not None
        assert result.startup_check_result.outcome.value == "READY"
        assert [row.symbol for row in UniverseCandidateRepository(conn).list_for_date(TRADE_DATE)] == ["035420"]
    finally:
        conn.close()


def test_prepare_and_check_blocks_on_unresolved_orders(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_unresolved_order(conn, order_repo)

        result = _make_service(conn).prepare_and_check(
            broker=_make_broker(),
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=True,
            run_startup_check=True,
        )

        assert result.outcome == PreopenReadinessOutcome.BLOCKED
        assert result.startup_check_result is not None
        assert result.startup_check_result.outcome.value == "BLOCKED"
        assert result.reason == "Unresolved orders exist. Startup is blocked."
    finally:
        conn.close()


def test_prepare_and_check_allows_unresolved_order_override(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_unresolved_order(conn, order_repo)
        _seed_existing_universe(conn, UniverseCandidateRepository(conn))

        result = _make_service(conn).prepare_and_check(
            broker=_make_broker(),
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=True,
            run_startup_check=True,
            allow_unresolved_orders=True,
        )

        assert result.outcome == PreopenReadinessOutcome.READY
        assert result.startup_check_result is not None
        assert result.startup_check_result.outcome.value == "READY"
    finally:
        conn.close()
