"""KIS daily-candle based universe source."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pandas as pd

from broker.base import BrokerInterface
from logger import get_logger
from market.universe_master import UniverseMasterItem
from market.universe_source import UniverseSourceInterface, UniverseSourceItem


def _require_trade_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"trade_date must be a string: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
    return value


class KisDailyUniverseSource(UniverseSourceInterface):
    """
    Build raw universe inputs from KIS daily candles and a local master list.

    Safety rules:
    - Only completed candles earlier than trade_date are used.
    - At least 20 completed candles are required per symbol.
    - Any symbol-level failure aborts the whole load to avoid partial snapshots.
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        master_items: Sequence[UniverseMasterItem],
        trade_date: str,
        daily_count: int = 40,
    ) -> None:
        self._broker = broker
        self._master_items = tuple(master_items)
        self._trade_date = _require_trade_date(trade_date)
        self._daily_count = self._validate_daily_count(daily_count)
        self._log = get_logger("scan")

    def load(self) -> list[UniverseSourceItem]:
        items: list[UniverseSourceItem] = []
        for master_item in self._master_items:
            if not isinstance(master_item, UniverseMasterItem):
                raise ValueError(
                    "master_items must contain only UniverseMasterItem instances."
                )

            self._log.debug(
                f"[kis_universe_source] loading daily candles: "
                f"symbol={master_item.symbol} trade_date={self._trade_date}"
            )
            try:
                df = self._broker.get_daily_candles(
                    master_item.symbol,
                    count=self._daily_count,
                    end_date=self._trade_date.replace("-", ""),
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load daily candles for symbol={master_item.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            items.append(self._build_source_item(master_item, df))

        return items

    @staticmethod
    def _validate_daily_count(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"daily_count must be an integer: {value!r}")
        if value < 20:
            raise ValueError(f"daily_count must be >= 20: {value!r}")
        return value

    def _build_source_item(
        self,
        master_item: UniverseMasterItem,
        df: pd.DataFrame,
    ) -> UniverseSourceItem:
        if not isinstance(df, pd.DataFrame):
            raise TypeError(
                f"daily candles must be a DataFrame: {type(df).__name__}"
            )

        required_columns = {"datetime", "close", "trade_value"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise ValueError(
                f"Daily candles are missing required columns for "
                f"symbol={master_item.symbol}: {missing_text}"
            )

        completed_rows = df[df["datetime"].dt.strftime("%Y-%m-%d") < self._trade_date]
        completed_rows = completed_rows.reset_index(drop=True)

        if len(completed_rows) < 20:
            raise ValueError(
                f"Not enough completed daily candles for symbol={master_item.symbol}: "
                f"required=20, actual={len(completed_rows)}"
            )

        recent_20 = completed_rows.tail(20).reset_index(drop=True)
        latest_row = recent_20.iloc[-1]
        avg_trade_value_20 = int(round(float(recent_20["trade_value"].mean())))

        return UniverseSourceItem(
            symbol=master_item.symbol,
            name=master_item.name,
            market=master_item.market,
            close_price=int(latest_row["close"]),
            prev_day_trade_value=int(latest_row["trade_value"]),
            avg_trade_value_20=avg_trade_value_20,
            is_managed=master_item.is_managed,
            is_investment_warning=master_item.is_investment_warning,
            is_investment_risk=master_item.is_investment_risk,
            is_attention_issue=master_item.is_attention_issue,
            is_disclosure_violation=master_item.is_disclosure_violation,
            is_liquidation_trade=master_item.is_liquidation_trade,
            is_trading_halt=master_item.is_trading_halt,
            is_rights_ex_date=master_item.is_rights_ex_date,
            is_preferred_stock=master_item.is_preferred_stock,
            is_etf=master_item.is_etf,
            is_etn=master_item.is_etn,
            is_spac=master_item.is_spac,
        )
