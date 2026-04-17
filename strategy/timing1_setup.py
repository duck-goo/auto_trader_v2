"""Read-only evaluator for buy timing 1 daily setup."""

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
class Timing1StrongDay:
    date: str
    open_price: int
    close_price: int
    prev_close: int
    gain_rate: float
    volume: int
    avg_volume_before: int
    volume_ratio: float


@dataclass(frozen=True)
class Timing1SetupSettings:
    strong_gain_rate: float = 0.15
    strong_volume_multiplier: float = 2.0
    strong_lookback_days: int = 5
    strong_volume_avg_window: int = 20
    ma_short_window: int = 20
    ma_long_window: int = 60
    ma_slope_lookback_days: int = 5

    def validated(self) -> "Timing1SetupSettings":
        strong_gain_rate = _require_positive_float(
            "strong_gain_rate",
            self.strong_gain_rate,
        )
        strong_volume_multiplier = _require_positive_float(
            "strong_volume_multiplier",
            self.strong_volume_multiplier,
        )
        strong_lookback_days = _require_positive_int(
            "strong_lookback_days",
            self.strong_lookback_days,
        )
        strong_volume_avg_window = _require_positive_int(
            "strong_volume_avg_window",
            self.strong_volume_avg_window,
        )
        ma_short_window = _require_positive_int(
            "ma_short_window",
            self.ma_short_window,
        )
        ma_long_window = _require_positive_int(
            "ma_long_window",
            self.ma_long_window,
        )
        ma_slope_lookback_days = _require_positive_int(
            "ma_slope_lookback_days",
            self.ma_slope_lookback_days,
        )
        if ma_long_window < ma_short_window:
            raise ValueError(
                "ma_long_window must be >= ma_short_window: "
                f"short={ma_short_window}, long={ma_long_window}"
            )
        return Timing1SetupSettings(
            strong_gain_rate=strong_gain_rate,
            strong_volume_multiplier=strong_volume_multiplier,
            strong_lookback_days=strong_lookback_days,
            strong_volume_avg_window=strong_volume_avg_window,
            ma_short_window=ma_short_window,
            ma_long_window=ma_long_window,
            ma_slope_lookback_days=ma_slope_lookback_days,
        )

    def min_required_completed_candles(self) -> int:
        trend_requirement = self.ma_long_window + self.ma_slope_lookback_days
        strong_day_requirement = (
            self.strong_volume_avg_window + self.strong_lookback_days + 1
        )
        return max(trend_requirement, strong_day_requirement)


@dataclass(frozen=True)
class Timing1SetupMatch:
    symbol: str
    evaluation_trade_date: str
    latest_daily_date: str
    latest_close: int
    ma_short_now: float
    ma_short_past: float
    ma_long_now: float
    ma_long_past: float
    strong_day: Timing1StrongDay


class Timing1SetupEvaluator:
    """
    Evaluate only the daily setup part of buy timing 1.

    Assumption:
        "오늘" daily moving averages are interpreted as the latest completed
        daily candle earlier than trade_date. This avoids using an incomplete
        intraday daily bar.
    """

    def evaluate(
        self,
        *,
        symbol: str,
        trade_date: str,
        daily_candles: pd.DataFrame,
        settings: Timing1SetupSettings,
    ) -> Timing1SetupMatch | None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string: {symbol!r}")
        normalized_trade_date = _require_trade_date(trade_date)
        normalized_settings = settings.validated()
        completed = self._normalize_completed_rows(
            daily_candles=daily_candles,
            trade_date=normalized_trade_date,
        )

        if len(completed) < normalized_settings.min_required_completed_candles():
            raise ValueError(
                f"Not enough completed daily candles for timing1 setup: "
                f"symbol={symbol}, required="
                f"{normalized_settings.min_required_completed_candles()}, "
                f"actual={len(completed)}"
            )

        ma_short = completed["close"].rolling(
            window=normalized_settings.ma_short_window
        ).mean()
        ma_long = completed["close"].rolling(
            window=normalized_settings.ma_long_window
        ).mean()

        latest_index = len(completed) - 1
        past_index = latest_index - normalized_settings.ma_slope_lookback_days

        ma_short_now = float(ma_short.iloc[latest_index])
        ma_short_past = float(ma_short.iloc[past_index])
        ma_long_now = float(ma_long.iloc[latest_index])
        ma_long_past = float(ma_long.iloc[past_index])

        if not (
            ma_short_now > ma_short_past and ma_long_now > ma_long_past
        ):
            return None

        strong_day = self._find_strong_day(
            completed=completed,
            settings=normalized_settings,
        )
        if strong_day is None:
            return None

        latest_row = completed.iloc[latest_index]
        latest_daily_date = completed.iloc[latest_index]["date_text"]
        return Timing1SetupMatch(
            symbol=symbol.strip(),
            evaluation_trade_date=normalized_trade_date,
            latest_daily_date=str(latest_daily_date),
            latest_close=int(latest_row["close"]),
            ma_short_now=ma_short_now,
            ma_short_past=ma_short_past,
            ma_long_now=ma_long_now,
            ma_long_past=ma_long_past,
            strong_day=strong_day,
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

        required_columns = {"datetime", "open", "close", "volume"}
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
            normalized.loc[:, "open"] = pd.to_numeric(
                normalized["open"],
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

    def _find_strong_day(
        self,
        *,
        completed: pd.DataFrame,
        settings: Timing1SetupSettings,
    ) -> Timing1StrongDay | None:
        search_start = max(1, len(completed) - settings.strong_lookback_days)

        for index in range(len(completed) - 1, search_start - 1, -1):
            row = completed.iloc[index]
            prev_row = completed.iloc[index - 1]

            prev_close = float(prev_row["close"])
            if prev_close <= 0:
                continue

            volume_window_start = index - settings.strong_volume_avg_window
            if volume_window_start < 0:
                continue

            avg_volume_before = float(
                completed.iloc[volume_window_start:index]["volume"].mean()
            )
            if avg_volume_before <= 0:
                continue

            open_price = float(row["open"])
            close_price = float(row["close"])
            volume = float(row["volume"])
            gain_rate = (close_price - prev_close) / prev_close
            volume_ratio = volume / avg_volume_before

            if gain_rate < settings.strong_gain_rate:
                continue
            if close_price <= open_price:
                continue
            if volume_ratio < settings.strong_volume_multiplier:
                continue

            return Timing1StrongDay(
                date=str(row["date_text"]),
                open_price=int(round(open_price)),
                close_price=int(round(close_price)),
                prev_close=int(round(prev_close)),
                gain_rate=float(gain_rate),
                volume=int(round(volume)),
                avg_volume_before=int(round(avg_volume_before)),
                volume_ratio=float(volume_ratio),
            )

        return None
