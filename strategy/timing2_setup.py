"""Read-only evaluator for buy timing 2 daily setup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from market import calculate_krx_upper_price_limit


def _require_trade_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"trade_date must be a string: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
    return value


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


@dataclass(frozen=True)
class Timing2SetupSettings:
    new_high_lookback_days: int = 60

    def validated(self) -> "Timing2SetupSettings":
        new_high_lookback_days = _require_positive_int(
            "new_high_lookback_days",
            self.new_high_lookback_days,
        )
        return Timing2SetupSettings(
            new_high_lookback_days=new_high_lookback_days,
        )

    def min_required_completed_candles(self) -> int:
        return self.new_high_lookback_days + 1


@dataclass(frozen=True)
class Timing2SetupMatch:
    symbol: str
    market: str
    evaluation_trade_date: str
    latest_daily_date: str
    latest_close: int
    previous_close: int
    official_upper_limit_price: int
    prior_lookback_high: int
    lookback_start_date: str
    lookback_end_date: str


class Timing2SetupEvaluator:
    """
    Evaluate only the daily setup part of buy timing 2.

    Conditions from the project spec:
    - previous completed daily close must exactly equal the official
      upper price limit
    - that close must be a strict new high over the configured lookback
      window
    """

    def evaluate(
        self,
        *,
        symbol: str,
        market: str,
        trade_date: str,
        daily_candles: pd.DataFrame,
        settings: Timing2SetupSettings,
    ) -> Timing2SetupMatch | None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string: {symbol!r}")
        if not isinstance(market, str) or not market.strip():
            raise ValueError(f"market must be a non-empty string: {market!r}")

        normalized_trade_date = _require_trade_date(trade_date)
        normalized_settings = settings.validated()
        completed = self._normalize_completed_rows(
            daily_candles=daily_candles,
            trade_date=normalized_trade_date,
        )

        if len(completed) < normalized_settings.min_required_completed_candles():
            raise ValueError(
                f"Not enough completed daily candles for timing2 setup: "
                f"symbol={symbol}, required="
                f"{normalized_settings.min_required_completed_candles()}, "
                f"actual={len(completed)}"
            )

        latest_index = len(completed) - 1
        latest_row = completed.iloc[latest_index]
        previous_row = completed.iloc[latest_index - 1]
        latest_close = int(round(float(latest_row["close"])))
        previous_close = int(round(float(previous_row["close"])))
        official_upper_limit_price = calculate_krx_upper_price_limit(
            market=market,
            base_price=previous_close,
        )

        if latest_close != official_upper_limit_price:
            return None

        lookback_start = latest_index - normalized_settings.new_high_lookback_days
        prior_window = completed.iloc[lookback_start:latest_index]
        prior_lookback_high = int(round(float(prior_window["high"].max())))
        if latest_close <= prior_lookback_high:
            return None

        return Timing2SetupMatch(
            symbol=symbol.strip(),
            market=market.strip(),
            evaluation_trade_date=normalized_trade_date,
            latest_daily_date=str(latest_row["date_text"]),
            latest_close=latest_close,
            previous_close=previous_close,
            official_upper_limit_price=official_upper_limit_price,
            prior_lookback_high=prior_lookback_high,
            lookback_start_date=str(prior_window.iloc[0]["date_text"]),
            lookback_end_date=str(prior_window.iloc[-1]["date_text"]),
        )

    def _normalize_completed_rows(
        self,
        *,
        daily_candles: pd.DataFrame,
        trade_date: str,
    ) -> pd.DataFrame:
        if not isinstance(daily_candles, pd.DataFrame):
            raise TypeError(
                f"daily_candles must be a DataFrame: {type(daily_candles).__name__}"
            )

        required_columns = {"datetime", "high", "close"}
        missing_columns = required_columns - set(daily_candles.columns)
        if missing_columns:
            raise ValueError(
                "Daily candles are missing required columns: "
                f"{', '.join(sorted(missing_columns))}"
            )

        normalized = daily_candles.copy(deep=True)
        try:
            normalized.loc[:, "datetime"] = pd.to_datetime(
                normalized["datetime"],
                errors="raise",
            )
            normalized.loc[:, "high"] = pd.to_numeric(
                normalized["high"],
                errors="raise",
            )
            normalized.loc[:, "close"] = pd.to_numeric(
                normalized["close"],
                errors="raise",
            )
        except Exception as exc:
            raise ValueError(
                "Daily candles contain non-numeric or non-datetime values."
            ) from exc

        normalized.loc[:, "date_text"] = normalized["datetime"].dt.strftime(
            "%Y-%m-%d"
        )
        completed = normalized[normalized["date_text"] < trade_date].copy()
        completed = completed.sort_values("datetime").reset_index(drop=True)
        return completed
