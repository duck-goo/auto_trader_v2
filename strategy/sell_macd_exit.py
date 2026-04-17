"""Read-only evaluator for sell MACD decrease on completed 15-minute bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


@dataclass(frozen=True)
class SellMacdExitSettings:
    fast_window: int = 12
    slow_window: int = 26
    signal_window: int = 9
    consecutive_decline_bars: int = 2

    def validated(self) -> "SellMacdExitSettings":
        fast_window = _require_positive_int("fast_window", self.fast_window)
        slow_window = _require_positive_int("slow_window", self.slow_window)
        signal_window = _require_positive_int("signal_window", self.signal_window)
        consecutive_decline_bars = _require_positive_int(
            "consecutive_decline_bars",
            self.consecutive_decline_bars,
        )
        if slow_window <= fast_window:
            raise ValueError(
                "slow_window must be greater than fast_window: "
                f"fast={fast_window}, slow={slow_window}"
            )
        return SellMacdExitSettings(
            fast_window=fast_window,
            slow_window=slow_window,
            signal_window=signal_window,
            consecutive_decline_bars=consecutive_decline_bars,
        )

    def min_required_bars(self) -> int:
        return self.slow_window + self.signal_window + self.consecutive_decline_bars


@dataclass(frozen=True)
class SellMacdExitMatch:
    symbol: str
    bar_start_at: str
    bar_end_at: str
    close_price: int
    macd_value: float
    signal_value: float
    hist_t_minus_2: float
    hist_t_minus_1: float
    hist_t: float
    consecutive_decline_bars: int


class SellMacdExitEvaluator:
    """
    Evaluate MACD histogram decrease on completed 15-minute bars.

    Rule from the project spec:
    - hist[t-2] > hist[t-1] > hist[t]
    - generalized here as N consecutive decreases using the latest N+1 bars
    """

    def evaluate(
        self,
        *,
        symbol: str,
        intraday_bars: pd.DataFrame,
        settings: SellMacdExitSettings,
    ) -> SellMacdExitMatch | None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string: {symbol!r}")

        normalized_settings = settings.validated()
        normalized = self._normalize_bars(intraday_bars)
        if len(normalized) < normalized_settings.min_required_bars():
            return None

        ema_fast = normalized["close"].ewm(
            span=normalized_settings.fast_window,
            adjust=False,
        ).mean()
        ema_slow = normalized["close"].ewm(
            span=normalized_settings.slow_window,
            adjust=False,
        ).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(
            span=normalized_settings.signal_window,
            adjust=False,
        ).mean()
        hist = macd - signal

        lookback_count = normalized_settings.consecutive_decline_bars + 1
        recent_hist = list(hist.tail(lookback_count))
        if len(recent_hist) != lookback_count:
            return None

        if not self._is_strictly_decreasing(recent_hist):
            return None

        latest = normalized.iloc[-1]
        hist_t_minus_2 = float(recent_hist[-3]) if lookback_count >= 3 else float("nan")
        hist_t_minus_1 = float(recent_hist[-2])
        hist_t = float(recent_hist[-1])

        return SellMacdExitMatch(
            symbol=symbol.strip(),
            bar_start_at=latest["bar_start_at"].isoformat(),
            bar_end_at=latest["bar_end_at"].isoformat(),
            close_price=int(latest["close"]),
            macd_value=float(macd.iloc[-1]),
            signal_value=float(signal.iloc[-1]),
            hist_t_minus_2=hist_t_minus_2,
            hist_t_minus_1=hist_t_minus_1,
            hist_t=hist_t,
            consecutive_decline_bars=normalized_settings.consecutive_decline_bars,
        )

    def _normalize_bars(self, intraday_bars: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(intraday_bars, pd.DataFrame):
            raise TypeError(
                f"intraday_bars must be a DataFrame: {type(intraday_bars).__name__}"
            )

        required_columns = {"bar_start_at", "bar_end_at", "close"}
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

    @staticmethod
    def _is_strictly_decreasing(values: list[float]) -> bool:
        if len(values) < 2:
            return False
        for index in range(1, len(values)):
            if not values[index - 1] > values[index]:
                return False
        return True
