"""Tests for PreopenUniverseService."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytz
import pytest

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import (
    MarketMasterRefreshItem,
    PreopenMarketMasterSource,
    PreopenUniverseResult,
    PreopenUniverseService,
    ServiceError,
    UniverseBuildOutcome,
    UniverseFilterSettings,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    MarketMasterEntry,
    MarketMasterRepository,
    UniverseCandidate,
    UniverseCandidateRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-14"


class FakeBroker(BrokerInterface):
    def __init__(self, candle_map: dict[str, pd.DataFrame]) -> None:
        self._candle_map = candle_map

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
        raise NotImplementedError

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


def _seed_existing_market_master(conn, repo):
    with transaction(conn):
        repo.replace_all(
            entries=[
                MarketMasterEntry(
                    symbol="035420",
                    name="NAVER",
                    market="KOSPI",
                ),
                MarketMasterEntry(
                    symbol="069500",
                    name="KODEX 200",
                    market="ETF",
                    is_etf=True,
                ),
            ],
            refreshed_at="2026-04-14T07:55:00+09:00",
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


def _make_conn(test_db_path):
    run_migrations(test_db_path)
    return get_connection(test_db_path)


def test_prepare_dry_run_refreshes_master_and_skips_universe_write(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        service = PreopenUniverseService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
            universe_repo=UniverseCandidateRepository(conn),
            now_fn=_fixed_now(),
        )
        broker = FakeBroker(
            {
                "035420": _daily_df(close_price=180000, trade_value=410_000_000_000),
                "069500": _daily_df(close_price=36250, trade_value=120_000_000_000),
            }
        )

        result = service.prepare(
            broker=broker,
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=False,
        )

        assert isinstance(result, PreopenUniverseResult)
        assert result.market_master_result.source == PreopenMarketMasterSource.REFRESHED
        assert result.market_master_result.symbol_count == 2
        assert result.market_master_result.refreshed_trade_date == TRADE_DATE
        assert result.market_master_result.is_same_trade_date is True
        assert result.market_master_result.validation_result.total_count == 2
        assert result.market_master_result.validation_result.is_valid is True
        assert [
            (row.name, row.count)
            for row in result.market_master_result.validation_result.market_counts
        ] == [("ETF", 1), ("KOSPI", 1)]
        positive_flags = {
            row.name: row.count
            for row in result.market_master_result.validation_result.flag_counts
            if row.count > 0
        }
        assert positive_flags == {
            "is_etf": 1,
        }
        assert result.source_item_count == 2
        assert result.universe_build_result.outcome == UniverseBuildOutcome.DRY_RUN
        assert UniverseCandidateRepository(conn).list_for_date(TRADE_DATE) == []
    finally:
        conn.close()


def test_prepare_write_saves_universe_snapshot(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        service = PreopenUniverseService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
            universe_repo=UniverseCandidateRepository(conn),
            now_fn=_fixed_now(),
        )
        broker = FakeBroker(
            {
                "035420": _daily_df(close_price=180000, trade_value=410_000_000_000),
                "069500": _daily_df(close_price=36250, trade_value=120_000_000_000),
            }
        )

        result = service.prepare(
            broker=broker,
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=True,
        )

        assert result.universe_build_result.outcome == UniverseBuildOutcome.SAVED
        assert [row.symbol for row in UniverseCandidateRepository(conn).list_for_date(TRADE_DATE)] == ["035420"]
    finally:
        conn.close()


def test_prepare_skips_empty_save_and_keeps_existing_universe(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        universe_repo = UniverseCandidateRepository(conn)
        _seed_existing_universe(conn, universe_repo)

        service = PreopenUniverseService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
            universe_repo=universe_repo,
            now_fn=_fixed_now(),
        )
        broker = FakeBroker(
            {
                "035420": _daily_df(close_price=180000, trade_value=80_000_000),
                "069500": _daily_df(close_price=36250, trade_value=120_000_000_000),
            }
        )

        result = service.prepare(
            broker=broker,
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            filter_settings=_settings(),
            write_universe=True,
            allow_empty_save=False,
        )

        assert result.universe_build_result.outcome == UniverseBuildOutcome.SKIPPED_EMPTY
        assert [row.symbol for row in universe_repo.list_for_date(TRADE_DATE)] == ["005930"]
    finally:
        conn.close()


def test_prepare_uses_existing_db_market_master_without_refresh(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        market_master_repo = MarketMasterRepository(conn)
        _seed_existing_market_master(conn, market_master_repo)

        service = PreopenUniverseService(
            conn=conn,
            market_master_repo=market_master_repo,
            universe_repo=UniverseCandidateRepository(conn),
            now_fn=_fixed_now(),
        )
        broker = FakeBroker(
            {
                "035420": _daily_df(close_price=180000, trade_value=410_000_000_000),
                "069500": _daily_df(close_price=36250, trade_value=120_000_000_000),
            }
        )

        result = service.prepare(
            broker=broker,
            trade_date=TRADE_DATE,
            use_existing_market_master=True,
            filter_settings=_settings(),
            write_universe=False,
        )

        assert result.market_master_result.source == PreopenMarketMasterSource.EXISTING_DB
        assert result.market_master_result.symbol_count == 2
        assert result.market_master_result.refreshed_at == "2026-04-14T07:55:00+09:00"
        assert result.market_master_result.refreshed_trade_date == TRADE_DATE
        assert result.market_master_result.is_same_trade_date is True
        assert result.market_master_result.validation_result.total_count == 2
        assert result.market_master_result.validation_result.is_valid is True
        positive_flags = {
            row.name: row.count
            for row in result.market_master_result.validation_result.flag_counts
            if row.count > 0
        }
        assert positive_flags == {
            "is_etf": 1,
        }
    finally:
        conn.close()


def test_prepare_includes_required_market_validation_warning(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        service = PreopenUniverseService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
            universe_repo=UniverseCandidateRepository(conn),
            now_fn=_fixed_now(),
        )
        broker = FakeBroker(
            {
                "035420": _daily_df(close_price=180000, trade_value=410_000_000_000),
                "069500": _daily_df(close_price=36250, trade_value=120_000_000_000),
            }
        )

        result = service.prepare(
            broker=broker,
            trade_date=TRADE_DATE,
            master_items=_master_items(),
            required_markets=["KOSPI", "KOSDAQ"],
            filter_settings=_settings(),
            write_universe=False,
        )

        assert result.market_master_result.validation_result.is_valid is False
        assert result.market_master_result.validation_result.warnings == (
            "required markets are missing: KOSDAQ",
        )
        assert result.universe_build_result.outcome == UniverseBuildOutcome.DRY_RUN
    finally:
        conn.close()


def test_prepare_blocks_when_existing_market_master_is_stale_and_same_day_required(
    test_db_path,
):
    conn = _make_conn(test_db_path)
    try:
        market_master_repo = MarketMasterRepository(conn)
        with transaction(conn):
            market_master_repo.replace_all(
                entries=[
                    MarketMasterEntry(
                        symbol="035420",
                        name="NAVER",
                        market="KOSPI",
                    )
                ],
                refreshed_at="2026-04-13T07:55:00+09:00",
            )

        service = PreopenUniverseService(
            conn=conn,
            market_master_repo=market_master_repo,
            universe_repo=UniverseCandidateRepository(conn),
            now_fn=_fixed_now(),
        )
        broker = FakeBroker(
            {
                "035420": _daily_df(close_price=180000, trade_value=410_000_000_000),
            }
        )

        with pytest.raises(ServiceError, match="Market master snapshot is stale"):
            service.prepare(
                broker=broker,
                trade_date=TRADE_DATE,
                use_existing_market_master=True,
                require_same_day_market_master=True,
                filter_settings=_settings(),
                write_universe=False,
            )
    finally:
        conn.close()


def test_prepare_blocks_when_market_master_count_is_below_minimum(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        service = PreopenUniverseService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
            universe_repo=UniverseCandidateRepository(conn),
            now_fn=_fixed_now(),
        )
        broker = FakeBroker(
            {
                "035420": _daily_df(close_price=180000, trade_value=410_000_000_000),
                "069500": _daily_df(close_price=36250, trade_value=120_000_000_000),
            }
        )

        with pytest.raises(ServiceError, match="symbol_count is below minimum"):
            service.prepare(
                broker=broker,
                trade_date=TRADE_DATE,
                master_items=_master_items(),
                min_market_master_count=3,
                filter_settings=_settings(),
                write_universe=False,
            )
    finally:
        conn.close()
