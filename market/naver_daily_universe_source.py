"""Naver chart based daily universe source."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pytz
import requests

from logger import get_logger
from market.universe_master import UniverseMasterItem
from market.universe_source import UniverseSourceInterface, UniverseSourceItem

_KST = pytz.timezone("Asia/Seoul")
_NAVER_CHART_URL = "https://fchart.stock.naver.com/sise.nhn"


def _require_trade_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"trade_date must be a string: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
    return value


@dataclass(frozen=True)
class NaverDailyUniverseSkippedItem:
    symbol: str
    name: str
    market: str
    error_type: str
    error_message: str


def parse_naver_daily_chart_xml(content: bytes) -> pd.DataFrame:
    """Parse Naver chart XML into normalized daily candles."""
    text = content.decode("euc-kr")
    root = ET.fromstring(text)
    rows: list[dict[str, object]] = []
    for item in root.findall(".//item"):
        raw_data = item.attrib.get("data")
        if raw_data is None:
            continue
        parts = raw_data.split("|")
        if len(parts) != 6:
            raise ValueError(f"Unexpected Naver chart item shape: {raw_data!r}")
        date_text, open_text, high_text, low_text, close_text, volume_text = parts
        candle_date = datetime.strptime(date_text, "%Y%m%d")
        open_price = int(open_text)
        high_price = int(high_text)
        low_price = int(low_text)
        close_price = int(close_text)
        volume = int(volume_text)
        typical_price = (open_price + high_price + low_price + close_price) / 4
        rows.append(
            {
                "datetime": _KST.localize(candle_date),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "trade_value": int(round(typical_price * volume)),
            }
        )
    return pd.DataFrame(rows)


class NaverDailyUniverseSource(UniverseSourceInterface):
    """
    Build raw universe inputs from Naver daily chart data.

    Naver chart XML provides OHLCV but not official trade value. This source uses
    typical price times volume as a liquidity estimate, so it should be treated
    as a fast pre-open fallback, not as an official KRX/KIS trade-value feed.
    """

    def __init__(
        self,
        *,
        master_items: Sequence[UniverseMasterItem],
        trade_date: str,
        daily_count: int = 40,
        session: requests.Session | None = None,
        timeout_seconds: float = 10.0,
        skip_symbol_errors: bool = False,
    ) -> None:
        self._master_items = tuple(master_items)
        self._trade_date = _require_trade_date(trade_date)
        self._daily_count = self._validate_daily_count(daily_count)
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds
        if not isinstance(skip_symbol_errors, bool):
            raise ValueError(
                f"skip_symbol_errors must be a bool: {skip_symbol_errors!r}"
            )
        self._skip_symbol_errors = skip_symbol_errors
        self._skipped_items: list[NaverDailyUniverseSkippedItem] = []
        self._log = get_logger("scan")

    @property
    def skipped_items(self) -> tuple[NaverDailyUniverseSkippedItem, ...]:
        return tuple(self._skipped_items)

    def load(self) -> list[UniverseSourceItem]:
        self._skipped_items = []
        items: list[UniverseSourceItem] = []
        for master_item in self._master_items:
            if not isinstance(master_item, UniverseMasterItem):
                raise ValueError(
                    "master_items must contain only UniverseMasterItem instances."
                )
            try:
                df = self._fetch_daily_candles(master_item.symbol)
                items.append(self._build_source_item(master_item, df))
            except Exception as exc:
                if not self._skip_symbol_errors:
                    raise
                self._record_skipped_item(master_item=master_item, exc=exc)
        return items

    @staticmethod
    def _validate_daily_count(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"daily_count must be an integer: {value!r}")
        if value < 20:
            raise ValueError(f"daily_count must be >= 20: {value!r}")
        return value

    def _fetch_daily_candles(self, symbol: str) -> pd.DataFrame:
        response = self._session.get(
            _NAVER_CHART_URL,
            params={
                "symbol": symbol,
                "timeframe": "day",
                "count": str(self._daily_count),
                "requestType": "0",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return parse_naver_daily_chart_xml(response.content)

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

    def _record_skipped_item(
        self,
        *,
        master_item: UniverseMasterItem,
        exc: Exception,
    ) -> None:
        skipped = NaverDailyUniverseSkippedItem(
            symbol=master_item.symbol,
            name=master_item.name,
            market=master_item.market,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        self._skipped_items.append(skipped)
        self._log.warning(
            "[naver_universe_source] skipped symbol: "
            f"symbol={skipped.symbol} error_type={skipped.error_type} "
            f"error_message={skipped.error_message}"
        )
