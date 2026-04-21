"""Read-only evaluator for buy timing 2 daily setup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


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


def _require_positive_float(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number: {value!r}")
    return float(value)


@dataclass(frozen=True)
class Timing2SetupSettings:
    close_high_lookback_days: int = 60
    close_gain_rate_threshold: float = 0.15
    volume_multiplier_threshold: float = 5.0

    def validated(self) -> "Timing2SetupSettings":
        close_high_lookback_days = _require_positive_int(
            "close_high_lookback_days",
            self.close_high_lookback_days,
        )
        close_gain_rate_threshold = _require_positive_float(
            "close_gain_rate_threshold",
            self.close_gain_rate_threshold,
        )
        volume_multiplier_threshold = _require_positive_float(
            "volume_multiplier_threshold",
            self.volume_multiplier_threshold,
        )
        return Timing2SetupSettings(
            close_high_lookback_days=close_high_lookback_days,
            close_gain_rate_threshold=close_gain_rate_threshold,
            volume_multiplier_threshold=volume_multiplier_threshold,
        )

    def min_required_completed_candles(self) -> int:
        return max(self.close_high_lookback_days, 2)


@dataclass(frozen=True)
class Timing2SetupMatch:
    symbol: str
    market: str
    evaluation_trade_date: str
    latest_daily_date: str
    latest_close: int
    previous_close: int
    latest_volume: int
    previous_volume: int
    close_gain_rate: float
    volume_ratio: float
    lookback_highest_close: int
    lookback_start_date: str
    lookback_end_date: str


class Timing2SetupEvaluator:
    """
    Evaluate only the daily setup part of buy timing 2.

    Conditions:
    - previous completed daily close must be the highest close in the
      configured lookback window including itself
    - previous completed daily close must be at least the configured gain
      threshold versus the day before
    - previous completed daily volume must be at least the configured
      multiplier versus the day before
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
        latest_volume = int(round(float(latest_row["volume"])))
        previous_volume = int(round(float(previous_row["volume"])))

        if previous_close <= 0 or previous_volume <= 0:
            return None

        lookback_start = latest_index - normalized_settings.close_high_lookback_days + 1
        lookback_window = completed.iloc[lookback_start : latest_index + 1]
        lookback_highest_close = int(round(float(lookback_window["close"].max())))
        if latest_close < lookback_highest_close:
            return None

        close_gain_rate = (latest_close / previous_close) - 1.0
        if close_gain_rate < normalized_settings.close_gain_rate_threshold:
            return None

        volume_ratio = latest_volume / previous_volume
        if volume_ratio < normalized_settings.volume_multiplier_threshold:
            return None

        return Timing2SetupMatch(
            symbol=symbol.strip(),
            market=market.strip(),
            evaluation_trade_date=normalized_trade_date,
            latest_daily_date=str(latest_row["date_text"]),
            latest_close=latest_close,
            previous_close=previous_close,
            latest_volume=latest_volume,
            previous_volume=previous_volume,
            close_gain_rate=float(close_gain_rate),
            volume_ratio=float(volume_ratio),
            lookback_highest_close=lookback_highest_close,
            lookback_start_date=str(lookback_window.iloc[0]["date_text"]),
            lookback_end_date=str(lookback_window.iloc[-1]["date_text"]),
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

        required_columns = {"datetime", "close", "volume"}
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
            normalized.loc[:, "close"] = pd.to_numeric(
                normalized["close"],
                errors="raise",
            )
            normalized.loc[:, "volume"] = pd.to_numeric(
                normalized["volume"],
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
