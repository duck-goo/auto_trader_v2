"""Helpers for resampling same-day minute candles into fixed bars."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytz


_KST = pytz.timezone("Asia/Seoul")
_BAR_COLUMNS = [
    "trade_date",
    "bar_start_at",
    "bar_end_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


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


def _require_time_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string: {value!r}")
    try:
        datetime.strptime(value, "%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"{name} must be HH:MM:SS: {value!r}") from exc
    return value


def _empty_bar_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_BAR_COLUMNS)


def resample_minute_candles_to_fixed_bars(
    *,
    minute_candles: pd.DataFrame,
    trade_date: str,
    observed_at: datetime,
    bar_minutes: int = 15,
    session_start_time: str = "09:00:00",
    session_end_time: str = "15:30:00",
) -> pd.DataFrame:
    """
    Convert same-day minute candles into completed fixed-minute bars.

    Assumptions:
    - `minute_candles` contains only trade-day minute data.
    - Bars are aligned to the regular KRX session start.
    - Only bars fully completed by `observed_at` are returned.
    """

    if not isinstance(minute_candles, pd.DataFrame):
        raise TypeError(
            f"minute_candles must be a DataFrame: {type(minute_candles).__name__}"
        )
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("observed_at must be a timezone-aware datetime.")

    normalized_trade_date = _require_trade_date(trade_date)
    normalized_bar_minutes = _require_positive_int("bar_minutes", bar_minutes)
    normalized_session_start = _require_time_text(
        "session_start_time",
        session_start_time,
    )
    normalized_session_end = _require_time_text(
        "session_end_time",
        session_end_time,
    )

    required_columns = {"datetime", "open", "high", "low", "close", "volume"}
    missing_columns = required_columns - set(minute_candles.columns)
    if missing_columns:
        raise ValueError(
            "minute_candles are missing required columns: "
            f"{', '.join(sorted(missing_columns))}"
        )

    normalized = minute_candles.copy(deep=True)
    try:
        normalized.loc[:, "datetime"] = pd.to_datetime(
            normalized["datetime"],
            errors="raise",
        )
        for column in ("open", "high", "low", "close", "volume"):
            normalized.loc[:, column] = pd.to_numeric(
                normalized[column],
                errors="raise",
            )
    except Exception as exc:
        raise ValueError(
            "minute_candles contain non-numeric or non-datetime values."
        ) from exc

    observed_at_kst = observed_at.astimezone(_KST)
    if observed_at_kst.strftime("%Y-%m-%d") != normalized_trade_date:
        raise ValueError(
            "observed_at date must match trade_date in KST: "
            f"trade_date={normalized_trade_date}, "
            f"observed_at={observed_at_kst.isoformat()}"
        )

    session_start = _KST.localize(
        datetime.strptime(
            f"{normalized_trade_date} {normalized_session_start}",
            "%Y-%m-%d %H:%M:%S",
        )
    )
    session_end = _KST.localize(
        datetime.strptime(
            f"{normalized_trade_date} {normalized_session_end}",
            "%Y-%m-%d %H:%M:%S",
        )
    )
    effective_observed_at = min(observed_at_kst, session_end)

    normalized.loc[:, "datetime"] = normalized["datetime"].dt.tz_convert(_KST)
    normalized.loc[:, "trade_date"] = normalized["datetime"].dt.strftime(
        "%Y-%m-%d"
    )
    filtered = normalized[
        (normalized["trade_date"] == normalized_trade_date)
        & (normalized["datetime"] >= session_start)
        & (normalized["datetime"] < session_end)
        & (normalized["datetime"] <= effective_observed_at)
    ].copy()
    if filtered.empty:
        return _empty_bar_df()

    bucket_seconds = normalized_bar_minutes * 60
    filtered.loc[:, "seconds_from_open"] = (
        filtered["datetime"] - session_start
    ).dt.total_seconds().astype("int64")
    filtered.loc[:, "bucket_index"] = (
        filtered["seconds_from_open"] // bucket_seconds
    ).astype("int64")
    filtered.loc[:, "bar_start_at"] = filtered["bucket_index"].map(
        lambda index: session_start + timedelta(seconds=index * bucket_seconds)
    )
    filtered.loc[:, "bar_end_at"] = filtered["bar_start_at"] + timedelta(
        seconds=bucket_seconds
    )

    completed = filtered[filtered["bar_end_at"] <= effective_observed_at].copy()
    if completed.empty:
        return _empty_bar_df()

    rows: list[dict[str, object]] = []
    for _, group in completed.groupby("bar_start_at", sort=True):
        sorted_group = group.sort_values("datetime").reset_index(drop=True)
        bar_start_at = sorted_group.iloc[0]["bar_start_at"]
        bar_end_at = sorted_group.iloc[0]["bar_end_at"]
        rows.append(
            {
                "trade_date": normalized_trade_date,
                "bar_start_at": bar_start_at.isoformat(),
                "bar_end_at": bar_end_at.isoformat(),
                "open": int(sorted_group.iloc[0]["open"]),
                "high": int(sorted_group["high"].max()),
                "low": int(sorted_group["low"].min()),
                "close": int(sorted_group.iloc[-1]["close"]),
                "volume": int(sorted_group["volume"].sum()),
            }
        )

    return pd.DataFrame(rows, columns=_BAR_COLUMNS)
