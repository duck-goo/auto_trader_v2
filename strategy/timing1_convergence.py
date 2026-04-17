"""Evaluator for buy timing 1 convergence on persisted 15-minute bars."""

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
class Timing1ConvergenceSettings:
    bar_minutes: int = 15
    ma_short_window: int = 20
    ma_long_window: int = 60
    convergence_threshold_rate: float = 0.02

    def validated(self) -> "Timing1ConvergenceSettings":
        bar_minutes = _require_positive_int("bar_minutes", self.bar_minutes)
        ma_short_window = _require_positive_int(
            "ma_short_window",
            self.ma_short_window,
        )
        ma_long_window = _require_positive_int(
            "ma_long_window",
            self.ma_long_window,
        )
        threshold = _require_positive_float(
            "convergence_threshold_rate",
            self.convergence_threshold_rate,
        )
        if ma_long_window < ma_short_window:
            raise ValueError(
                "ma_long_window must be >= ma_short_window: "
                f"short={ma_short_window}, long={ma_long_window}"
            )
        return Timing1ConvergenceSettings(
            bar_minutes=bar_minutes,
            ma_short_window=ma_short_window,
            ma_long_window=ma_long_window,
            convergence_threshold_rate=threshold,
        )

    def min_required_bars(self) -> int:
        return self.ma_long_window


@dataclass(frozen=True)
class Timing1ConvergenceMatch:
    symbol: str
    trade_date: str
    strong_day_date: str
    convergence_trade_date: str
    bar_start_at: str
    bar_end_at: str
    close_price: int
    ma_short: float
    ma_long: float
    convergence_threshold_rate: float
    convergence_spread: float
    day_high: int


class Timing1ConvergenceEvaluator:
    """
    Evaluate timing1 convergence using persisted 15-minute bars.

    Safety rule:
    - This evaluator does not invent historical intraday bars.
    - If there are not enough persisted bars to compute 60-period MA,
      it returns None instead of approximating.
    """

    def evaluate(
        self,
        *,
        symbol: str,
        trade_date: str,
        strong_day_date: str,
        intraday_bars: pd.DataFrame,
        settings: Timing1ConvergenceSettings,
    ) -> Timing1ConvergenceMatch | None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string: {symbol!r}")
        normalized_trade_date = _require_trade_date(trade_date)
        normalized_strong_day_date = _require_trade_date(strong_day_date)
        normalized_settings = settings.validated()

        if normalized_strong_day_date >= normalized_trade_date:
            return None

        normalized = self._normalize_bars(intraday_bars)
        if len(normalized) < normalized_settings.min_required_bars():
            return None

        ma_short = normalized["close"].rolling(
            window=normalized_settings.ma_short_window
        ).mean()
        ma_long = normalized["close"].rolling(
            window=normalized_settings.ma_long_window
        ).mean()

        normalized.loc[:, "ma_short"] = ma_short
        normalized.loc[:, "ma_long"] = ma_long
        normalized.loc[:, "bar_trade_date"] = normalized["bar_start_at"].dt.strftime(
            "%Y-%m-%d"
        )

        eligible = normalized[
            (normalized["bar_trade_date"] > normalized_strong_day_date)
            & (normalized["bar_trade_date"] <= normalized_trade_date)
        ].copy()
        if eligible.empty:
            return None

        for _, row in eligible.iterrows():
            if pd.isna(row["ma_short"]) or pd.isna(row["ma_long"]):
                continue

            close_price = float(row["close"])
            ma_short_value = float(row["ma_short"])
            ma_long_value = float(row["ma_long"])
            if close_price <= 0:
                continue
            if close_price < ma_short_value or close_price < ma_long_value:
                continue

            spread = max(close_price, ma_short_value, ma_long_value) - min(
                close_price,
                ma_short_value,
                ma_long_value,
            )
            if spread > close_price * normalized_settings.convergence_threshold_rate:
                continue

            convergence_trade_date = str(row["bar_trade_date"])
            day_high = int(
                normalized.loc[
                    normalized["bar_trade_date"] == convergence_trade_date,
                    "high",
                ].max()
            )
            return Timing1ConvergenceMatch(
                symbol=symbol.strip(),
                trade_date=normalized_trade_date,
                strong_day_date=normalized_strong_day_date,
                convergence_trade_date=convergence_trade_date,
                bar_start_at=row["bar_start_at"].isoformat(),
                bar_end_at=row["bar_end_at"].isoformat(),
                close_price=int(row["close"]),
                ma_short=ma_short_value,
                ma_long=ma_long_value,
                convergence_threshold_rate=(
                    normalized_settings.convergence_threshold_rate
                ),
                convergence_spread=spread,
                day_high=day_high,
            )
        return None

    def _normalize_bars(self, intraday_bars: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(intraday_bars, pd.DataFrame):
            raise TypeError(
                f"intraday_bars must be a DataFrame: {type(intraday_bars).__name__}"
            )

        required_columns = {"bar_start_at", "bar_end_at", "high", "close"}
        missing_columns = required_columns - set(intraday_bars.columns)
        if missing_columns:
            raise ValueError(
                "intraday_bars are missing required columns: "
                f"{', '.join(sorted(missing_columns))}"
            )

        normalized = intraday_bars.copy(deep=True)
        try:
            normalized = normalized.assign(
                bar_start_at=pd.to_datetime(
                    normalized["bar_start_at"],
                    errors="raise",
                ),
                bar_end_at=pd.to_datetime(
                    normalized["bar_end_at"],
                    errors="raise",
                ),
                high=pd.to_numeric(
                    normalized["high"],
                    errors="raise",
                ),
                close=pd.to_numeric(
                    normalized["close"],
                    errors="raise",
                ),
            )
        except Exception as exc:
            raise ValueError(
                "intraday_bars contain non-numeric or non-datetime values."
            ) from exc

        return normalized.sort_values("bar_end_at").reset_index(drop=True)
