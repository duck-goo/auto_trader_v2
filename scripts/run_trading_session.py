"""
Run the whole trading session in one command.

Flow:
1. Prepare preopen universe and run startup gate.
2. If startup is READY, start intraday trading polling.

Safety:
- This launcher reuses the existing preopen and polling scripts instead of
  duplicating business logic.
- Preview mode still writes the universe snapshot during preopen because the
  later polling phase depends on persisted universe/startup state.
- Execute mode checks whether another trading polling loop is already running
  before preopen begins.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging
from services import RuntimeLockBusyError, RuntimeLockService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import RuntimeLockRepository
from strategy import (
    BUY_STRATEGY_CHOICES,
    BUY_STRATEGY_BOTH,
    BUY_STRATEGY_TIMING2,
    DEFAULT_TIMING2_SELL_COST_RATE,
    Timing2ThirtySecondTriggerSettings,
    resolve_buy_strategy_selection,
    selection_to_buy_strategy,
)

KST = pytz.timezone("Asia/Seoul")
DEFAULT_POLLING_LOCK_PREFIX = "intraday_trading_polling"


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run preopen preparation, then intraday trading polling."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--master-input",
        help="Path to JSON or CSV market master items.",
    )
    parser.add_argument(
        "--master-format",
        default="auto",
        choices=("auto", "json", "csv"),
        help="Market master input format. Default: auto",
    )
    parser.add_argument(
        "--use-db-master",
        action="store_true",
        help="Use the current market master snapshot already stored in SQLite.",
    )
    parser.add_argument(
        "--require-same-day-master",
        action="store_true",
        help="Block the run if market master refreshed date does not match trade_date.",
    )
    parser.add_argument(
        "--min-master-count",
        type=int,
        default=None,
        help="Optional minimum allowed symbol count for market master.",
    )
    parser.add_argument(
        "--required-market",
        action="append",
        default=[],
        help="Required market code. Repeat for multiple values.",
    )
    parser.add_argument(
        "--allow-validation-failures",
        action="store_true",
        help="Continue even if market master validation emits warnings.",
    )
    parser.add_argument(
        "--daily-count",
        type=int,
        default=40,
        help="How many daily candles to request per symbol in preopen. Default: 40",
    )
    parser.add_argument(
        "--min-price",
        type=int,
        default=5_000,
        help="Minimum allowed close price. Default: 5000",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        default=200_000,
        help="Maximum allowed close price. Default: 200000",
    )
    parser.add_argument(
        "--min-avg-trade-value-20",
        type=int,
        default=100_000_000,
        help="Minimum 20-day average trade value. Default: 100000000",
    )
    parser.add_argument(
        "--allow-unresolved-orders",
        action="store_true",
        help="Allow startup check to continue even if unresolved orders exist.",
    )
    parser.add_argument(
        "--allow-empty-save",
        action="store_true",
        help="Allow saving an empty universe snapshot when accepted_count is 0.",
    )
    parser.add_argument(
        "--preopen-scan-timing1-setup",
        action="store_true",
        help="Run timing1 daily setup scan after preopen universe save.",
    )
    parser.add_argument(
        "--preopen-write-timing1-signals",
        action="store_true",
        help="Persist timing1 daily setup signals during preopen.",
    )
    parser.add_argument(
        "--preopen-timing1-daily-count",
        type=int,
        default=90,
        help="Daily candle count for timing1 setup scan. Default: 90",
    )
    parser.add_argument(
        "--preopen-scan-timing2-setup",
        action="store_true",
        help="Run timing2 daily setup scan after preopen universe save.",
    )
    parser.add_argument(
        "--preopen-write-timing2-signals",
        action="store_true",
        help="Persist timing2 daily setup signals during preopen.",
    )
    parser.add_argument(
        "--preopen-timing2-daily-count",
        type=int,
        default=90,
        help="Daily candle count for timing2 setup scan. Default: 90",
    )
    parser.add_argument(
        "--preopen-timing2-new-high-lookback-days",
        type=int,
        default=60,
        help="Timing2 setup lookback window. Default: 60",
    )
    parser.add_argument(
        "--scan-timing1",
        action="store_true",
        help="Run timing1 intraday trigger scan during polling.",
    )
    parser.add_argument(
        "--scan-timing2",
        action="store_true",
        help="Run timing2 intraday trigger scan during polling.",
    )
    parser.add_argument(
        "--buy-strategy",
        choices=BUY_STRATEGY_CHOICES,
        default=None,
        help=(
            "Buy strategy selection for future UI controls. "
            "Choices: timing1, timing2, both. Default: legacy scan flags, or both."
        ),
    )
    parser.add_argument(
        "--per-order-budget",
        type=int,
        required=True,
        help="Max KRW budget per buy order.",
    )
    parser.add_argument(
        "--max-holdings",
        type=int,
        required=True,
        help="Max concurrent holdings/unresolved-buy symbols.",
    )
    parser.add_argument(
        "--max-daily-order-count",
        type=int,
        default=None,
        help="Optional max total order count for the trade date. New buys are blocked once reached.",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=int,
        default=None,
        help="Optional max realized daily loss in KRW. New buys are blocked once reached.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Stale order timeout seconds for maintenance. Default: 300",
    )
    parser.add_argument(
        "--buy-signal-limit",
        type=int,
        default=200,
        help="How many unacted buy signals to inspect. Default: 200",
    )
    parser.add_argument(
        "--sell-signal-limit",
        type=int,
        default=200,
        help="How many unacted sell signals to inspect. Default: 200",
    )
    parser.add_argument(
        "--sell-stop-loss-percent",
        type=float,
        default=3.0,
        help="Sell stop-loss percent. Default: 3.0",
    )
    parser.add_argument(
        "--sell-take-profit-percent",
        type=float,
        default=5.0,
        help="Sell take-profit percent. Default: 5.0",
    )
    parser.add_argument(
        "--sell-macd-fast-window",
        type=int,
        default=12,
        help="Sell MACD fast EMA window. Default: 12",
    )
    parser.add_argument(
        "--sell-macd-slow-window",
        type=int,
        default=26,
        help="Sell MACD slow EMA window. Default: 26",
    )
    parser.add_argument(
        "--sell-macd-signal-window",
        type=int,
        default=9,
        help="Sell MACD signal EMA window. Default: 9",
    )
    parser.add_argument(
        "--sell-macd-consecutive-decline-bars",
        type=int,
        default=2,
        help="Sell MACD consecutive decline bars. Default: 2",
    )
    parser.add_argument(
        "--sell-macd-history-limit",
        type=int,
        default=300,
        help="Persisted 15m bar history limit for sell MACD scan. Default: 300",
    )
    parser.add_argument(
        "--timing2-lot-stop-loss-percent",
        type=float,
        default=1.5,
        help="Timing2 lot stop-loss percent after sell costs. Default: 1.5",
    )
    parser.add_argument(
        "--timing2-lot-take-profit-percent",
        type=float,
        default=5.0,
        help="Timing2 lot partial take-profit percent. Default: 5.0",
    )
    parser.add_argument(
        "--timing2-lot-partial-take-profit-percent",
        type=float,
        default=50.0,
        help="Timing2 lot partial take-profit sell ratio percent. Default: 50.0",
    )
    parser.add_argument(
        "--timing2-lot-sell-cost-rate",
        type=float,
        default=DEFAULT_TIMING2_SELL_COST_RATE,
        help=(
            "Timing2 lot combined sell fee/tax ratio. "
            f"Default: {DEFAULT_TIMING2_SELL_COST_RATE}"
        ),
    )
    parser.add_argument(
        "--timing2-30s-min-samples-per-bar",
        type=int,
        default=2,
        help="Minimum samples required to build one Timing2 30s bar. Default: 2",
    )
    parser.add_argument(
        "--timing2-max-sample-symbols-per-cycle",
        type=int,
        default=30,
        help="Max Timing2 symbols to sample per cycle. Default: 30",
    )
    parser.add_argument(
        "--timing2-30s-morning-start-time",
        default="09:00:00",
        help="Timing2 30s morning pattern start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing2-30s-morning-end-time",
        default="10:00:00",
        help="Timing2 30s morning pattern end time HH:MM:SS. Default: 10:00:00",
    )
    parser.add_argument(
        "--timing2-30s-range-breakout-start-time",
        default="10:00:00",
        help="Timing2 30s range breakout start time HH:MM:SS. Default: 10:00:00",
    )
    parser.add_argument(
        "--buy-start-time",
        default="09:00:00",
        help="Buy execution start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--buy-cutoff-time",
        default="12:00:00",
        help="Buy execution cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--sell-start-time",
        default="09:00:00",
        help="Sell execution start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--sell-cutoff-time",
        default="15:20:00",
        help="Sell execution cutoff time HH:MM:SS. Default: 15:20:00",
    )
    parser.add_argument(
        "--timing1-start-time",
        default="09:00:00",
        help="Timing1 monitoring start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing1-cutoff-time",
        default="12:00:00",
        help="Timing1 monitoring cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--timing1-daily-count",
        type=int,
        default=5,
        help="Timing1 daily count for next-trading-day validation. Default: 5",
    )
    parser.add_argument(
        "--timing2-tolerance-rate",
        type=float,
        default=0.003,
        help="Timing2 trigger tolerance rate. Default: 0.003",
    )
    parser.add_argument(
        "--timing2-start-time",
        default="09:00:00",
        help="Timing2 monitoring start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing2-cutoff-time",
        default="12:00:00",
        help="Timing2 monitoring cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=20,
        help="Polling interval in seconds. Default: 20",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Maximum cycle count. 0 means keep polling until stop condition. Default: 0",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=3,
        help="Stop after this many consecutive failed cycles. Default: 3",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually place buy/sell orders during intraday polling.",
    )
    parser.add_argument(
        "--polling-lock-name",
        default=None,
        help="Optional runtime lock name for intraday polling execute mode.",
    )
    parser.add_argument(
        "--polling-lock-lease-seconds",
        type=int,
        default=0,
        help="Polling runtime lock lease seconds. 0 uses polling default.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional combined JSON output path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _validate_positive_int(
    name: str,
    value: int,
    *,
    allow_zero: bool = False,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer: {value!r}")
    if allow_zero:
        if value < 0:
            raise ValueError(f"{name} must be >= 0: {value!r}")
    elif value <= 0:
        raise ValueError(f"{name} must be > 0: {value!r}")
    return value


def _resolve_polling_lock_name(args: argparse.Namespace) -> str:
    if isinstance(args.polling_lock_name, str) and args.polling_lock_name.strip():
        return args.polling_lock_name.strip()
    return f"{DEFAULT_POLLING_LOCK_PREFIX}:{args.trade_date}"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _append_option(command: list[str], flag: str, value: Any | None) -> None:
    if value is None:
        return
    command.extend([flag, str(value)])


def _append_repeatable_option(
    command: list[str],
    flag: str,
    values: list[str],
) -> None:
    for value in values:
        command.extend([flag, value])


def _resolve_scan_selection(args: argparse.Namespace) -> tuple[bool, bool]:
    return resolve_buy_strategy_selection(
        buy_strategy=args.buy_strategy,
        scan_timing1=args.scan_timing1,
        scan_timing2=args.scan_timing2,
    )


def _explicit_timing2_intraday_requested(args: argparse.Namespace) -> bool:
    return args.scan_timing2 or args.buy_strategy in (
        BUY_STRATEGY_TIMING2,
        BUY_STRATEGY_BOTH,
    )


def _check_timing2_preopen_setup_args(args: argparse.Namespace) -> None:
    if args.preopen_write_timing2_signals and not args.preopen_scan_timing2_setup:
        raise ValueError(
            "--preopen-write-timing2-signals requires --preopen-scan-timing2-setup."
        )

    if not _explicit_timing2_intraday_requested(args):
        return

    if args.preopen_scan_timing2_setup and args.preopen_write_timing2_signals:
        return

    raise ValueError(
        "Explicit Timing2 selection requires --preopen-scan-timing2-setup "
        "and --preopen-write-timing2-signals so setup candidates are persisted "
        "before polling starts."
    )


def _build_preopen_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "prepare_preopen_universe.py"),
        "--trade-date",
        args.trade_date,
        "--daily-count",
        str(args.daily_count),
        "--min-price",
        str(args.min_price),
        "--max-price",
        str(args.max_price),
        "--min-avg-trade-value-20",
        str(args.min_avg_trade_value_20),
        "--write-universe",
        "--run-startup-check",
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]
    if args.use_db_master:
        command.append("--use-db-master")
    else:
        command.extend(["--master-input", str(_resolve_path(args.master_input))])
        command.extend(["--master-format", args.master_format])
    if args.require_same_day_master:
        command.append("--require-same-day-master")
    if args.min_master_count is not None:
        command.extend(["--min-master-count", str(args.min_master_count)])
    _append_repeatable_option(command, "--required-market", list(args.required_market))
    if args.allow_validation_failures:
        command.append("--allow-validation-failures")
    if args.allow_unresolved_orders:
        command.append("--allow-unresolved-orders")
    if args.allow_empty_save:
        command.append("--allow-empty-save")
    if args.preopen_scan_timing1_setup:
        command.append("--scan-timing1-setup")
    if args.preopen_write_timing1_signals:
        command.append("--write-timing1-signals")
    command.extend(
        ["--timing1-daily-count", str(args.preopen_timing1_daily_count)]
    )
    if args.preopen_scan_timing2_setup:
        command.append("--scan-timing2-setup")
    if args.preopen_write_timing2_signals:
        command.append("--write-timing2-signals")
    command.extend(
        ["--timing2-daily-count", str(args.preopen_timing2_daily_count)]
    )
    command.extend(
        [
            "--timing2-new-high-lookback-days",
            str(args.preopen_timing2_new_high_lookback_days),
        ]
    )
    return command


def _build_polling_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
) -> list[str]:
    _resolve_scan_selection(args)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_intraday_trading_polling.py"),
        "--trade-date",
        args.trade_date,
        "--per-order-budget",
        str(args.per_order_budget),
        "--max-holdings",
        str(args.max_holdings),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--buy-signal-limit",
        str(args.buy_signal_limit),
        "--sell-signal-limit",
        str(args.sell_signal_limit),
        "--sell-stop-loss-percent",
        str(args.sell_stop_loss_percent),
        "--sell-take-profit-percent",
        str(args.sell_take_profit_percent),
        "--sell-macd-fast-window",
        str(args.sell_macd_fast_window),
        "--sell-macd-slow-window",
        str(args.sell_macd_slow_window),
        "--sell-macd-signal-window",
        str(args.sell_macd_signal_window),
        "--sell-macd-consecutive-decline-bars",
        str(args.sell_macd_consecutive_decline_bars),
        "--sell-macd-history-limit",
        str(args.sell_macd_history_limit),
        "--timing2-lot-stop-loss-percent",
        str(args.timing2_lot_stop_loss_percent),
        "--timing2-lot-take-profit-percent",
        str(args.timing2_lot_take_profit_percent),
        "--timing2-lot-partial-take-profit-percent",
        str(args.timing2_lot_partial_take_profit_percent),
        "--timing2-lot-sell-cost-rate",
        str(args.timing2_lot_sell_cost_rate),
        "--timing2-30s-min-samples-per-bar",
        str(args.timing2_30s_min_samples_per_bar),
        "--timing2-max-sample-symbols-per-cycle",
        str(args.timing2_max_sample_symbols_per_cycle),
        "--timing2-30s-morning-start-time",
        args.timing2_30s_morning_start_time,
        "--timing2-30s-morning-end-time",
        args.timing2_30s_morning_end_time,
        "--timing2-30s-range-breakout-start-time",
        args.timing2_30s_range_breakout_start_time,
        "--buy-start-time",
        args.buy_start_time,
        "--buy-cutoff-time",
        args.buy_cutoff_time,
        "--sell-start-time",
        args.sell_start_time,
        "--sell-cutoff-time",
        args.sell_cutoff_time,
        "--timing1-start-time",
        args.timing1_start_time,
        "--timing1-cutoff-time",
        args.timing1_cutoff_time,
        "--timing1-daily-count",
        str(args.timing1_daily_count),
        "--timing2-tolerance-rate",
        str(args.timing2_tolerance_rate),
        "--timing2-start-time",
        args.timing2_start_time,
        "--timing2-cutoff-time",
        args.timing2_cutoff_time,
        "--interval-seconds",
        str(args.interval_seconds),
        "--max-cycles",
        str(args.max_cycles),
        "--max-consecutive-failures",
        str(args.max_consecutive_failures),
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]
    if args.max_daily_order_count is not None:
        command.extend(
            [
                "--max-daily-order-count",
                str(args.max_daily_order_count),
            ]
        )
    if args.max_daily_loss is not None:
        command.extend(
            [
                "--max-daily-loss",
                str(args.max_daily_loss),
            ]
        )
    if args.buy_strategy is not None:
        command.extend(["--buy-strategy", args.buy_strategy])
    elif args.scan_timing1:
        command.append("--scan-timing1")
    if args.buy_strategy is None and args.scan_timing2:
        command.append("--scan-timing2")
    if args.execute:
        command.append("--execute")
    if isinstance(args.polling_lock_name, str) and args.polling_lock_name.strip():
        command.extend(["--lock-name", args.polling_lock_name.strip()])
    if args.polling_lock_lease_seconds > 0:
        command.extend(
            ["--lock-lease-seconds", str(args.polling_lock_lease_seconds)]
        )
    return command


def _run_child(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    return int(completed.returncode)


def _build_payload(
    *,
    trade_date: str,
    execute_mode: bool,
    started_at: str,
    finished_at: str,
    session_outcome: str,
    session_reason: str | None,
    preopen_exit_code: int | None,
    preopen_result: dict[str, Any] | None,
    polling_exit_code: int | None,
    polling_result: dict[str, Any] | None,
    polling_started: bool,
    polling_lock_name: str,
    output_path: Path | None,
    buy_strategy: str | None = None,
    run_timing1: bool | None = None,
    run_timing2: bool | None = None,
) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "execute_mode": execute_mode,
        "buy_strategy": buy_strategy,
        "run_timing1": run_timing1,
        "run_timing2": run_timing2,
        "started_at": started_at,
        "finished_at": finished_at,
        "session_outcome": session_outcome,
        "session_reason": session_reason,
        "polling_lock_name": polling_lock_name,
        "polling_started": polling_started,
        "preopen_exit_code": preopen_exit_code,
        "preopen_result": preopen_result,
        "polling_exit_code": polling_exit_code,
        "polling_result": polling_result,
        "output_path": None if output_path is None else str(output_path),
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _check_master_source_args(args: argparse.Namespace) -> None:
    has_master_input = bool(args.master_input)
    if args.use_db_master == has_master_input:
        raise ValueError(
            "Exactly one of --master-input or --use-db-master must be provided."
        )


def _precheck_polling_lock(
    *,
    execute_mode: bool,
    db_path: str,
    busy_timeout_ms: int,
    lock_name: str,
    lease_seconds: int,
) -> None:
    if not execute_mode:
        return

    run_migrations(db_path)
    conn = get_connection(
        db_path,
        busy_timeout_ms=busy_timeout_ms,
    )
    try:
        lock_service = RuntimeLockService(
            conn=conn,
            lock_repo=RuntimeLockRepository(conn),
        )
        lock_service.acquire(
            lock_name=lock_name,
            lease_seconds=lease_seconds,
        )
        lock_service.release(lock_name=lock_name)
    finally:
        conn.close()


def main() -> int:
    args = _parse_args()

    try:
        _check_master_source_args(args)
        run_timing1, run_timing2 = _resolve_scan_selection(args)
        _check_timing2_preopen_setup_args(args)
        _validate_positive_int("daily_count", args.daily_count)
        _validate_positive_int("min_price", args.min_price)
        _validate_positive_int("max_price", args.max_price)
        _validate_positive_int(
            "min_avg_trade_value_20",
            args.min_avg_trade_value_20,
        )
        _validate_positive_int(
            "preopen_timing1_daily_count",
            args.preopen_timing1_daily_count,
        )
        _validate_positive_int(
            "preopen_timing2_daily_count",
            args.preopen_timing2_daily_count,
        )
        _validate_positive_int(
            "preopen_timing2_new_high_lookback_days",
            args.preopen_timing2_new_high_lookback_days,
        )
        _validate_positive_int("per_order_budget", args.per_order_budget)
        _validate_positive_int("max_holdings", args.max_holdings)
        if args.max_daily_loss is not None:
            _validate_positive_int("max_daily_loss", args.max_daily_loss)
        _validate_positive_int("timeout_seconds", args.timeout_seconds)
        _validate_positive_int("buy_signal_limit", args.buy_signal_limit)
        _validate_positive_int("sell_signal_limit", args.sell_signal_limit)
        _validate_positive_int(
            "sell_macd_fast_window",
            args.sell_macd_fast_window,
        )
        _validate_positive_int(
            "sell_macd_slow_window",
            args.sell_macd_slow_window,
        )
        _validate_positive_int(
            "sell_macd_signal_window",
            args.sell_macd_signal_window,
        )
        _validate_positive_int(
            "sell_macd_consecutive_decline_bars",
            args.sell_macd_consecutive_decline_bars,
        )
        _validate_positive_int(
            "sell_macd_history_limit",
            args.sell_macd_history_limit,
        )
        _validate_positive_int(
            "timing2_30s_min_samples_per_bar",
            args.timing2_30s_min_samples_per_bar,
        )
        _validate_positive_int(
            "timing2_max_sample_symbols_per_cycle",
            args.timing2_max_sample_symbols_per_cycle,
        )
        Timing2ThirtySecondTriggerSettings(
            morning_start_time=args.timing2_30s_morning_start_time,
            morning_end_time=args.timing2_30s_morning_end_time,
            range_breakout_start_time=args.timing2_30s_range_breakout_start_time,
        ).validated()
        _validate_positive_int("timing1_daily_count", args.timing1_daily_count)
        _validate_positive_int("interval_seconds", args.interval_seconds)
        _validate_positive_int("max_cycles", args.max_cycles, allow_zero=True)
        _validate_positive_int(
            "max_consecutive_failures",
            args.max_consecutive_failures,
        )
        _validate_positive_int(
            "polling_lock_lease_seconds",
            args.polling_lock_lease_seconds,
            allow_zero=True,
        )

        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None
    polling_lock_name = _resolve_polling_lock_name(args)
    precheck_lock_lease = (
        args.polling_lock_lease_seconds
        if args.polling_lock_lease_seconds > 0
        else max(args.interval_seconds * 3, 90)
    )

    _section("Run Trading Session")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("execute", str(args.execute))
    _ok(
        "buy_strategy",
        selection_to_buy_strategy(
            run_timing1=run_timing1,
            run_timing2=run_timing2,
        ),
    )
    _ok("db_path", str(db_path))
    _ok("polling_lock_name", polling_lock_name)
    _ok(
        "max_daily_order_count",
        "-" if args.max_daily_order_count is None else str(args.max_daily_order_count),
    )
    _ok(
        "max_daily_loss",
        "-" if args.max_daily_loss is None else str(args.max_daily_loss),
    )
    if not args.execute:
        _warn(
            "session_note",
            "Preopen still writes universe/startup state even in preview mode "
            "because the later polling phase depends on persisted session state.",
        )

    started_at = datetime.now(KST).isoformat()
    preopen_exit_code: int | None = None
    preopen_result: dict[str, Any] | None = None
    polling_exit_code: int | None = None
    polling_result: dict[str, Any] | None = None
    polling_started = False
    session_outcome = "UNKNOWN"
    session_reason: str | None = None

    try:
        _precheck_polling_lock(
            execute_mode=args.execute,
            db_path=db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
            lock_name=polling_lock_name,
            lease_seconds=precheck_lock_lease,
        )
    except RuntimeLockBusyError as exc:
        session_outcome = "POLLING_LOCK_BUSY"
        session_reason = str(exc)
        _fail("polling_lock", session_reason)
        if output_path is not None:
            payload = _build_payload(
                trade_date=args.trade_date,
                execute_mode=args.execute,
                started_at=started_at,
                finished_at=datetime.now(KST).isoformat(),
                session_outcome=session_outcome,
                session_reason=session_reason,
                preopen_exit_code=None,
                preopen_result=None,
                polling_exit_code=None,
                polling_result=None,
                polling_started=False,
                polling_lock_name=polling_lock_name,
                output_path=output_path,
                buy_strategy=selection_to_buy_strategy(
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                ),
                run_timing1=run_timing1,
                run_timing2=run_timing2,
            )
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))
        return 4
    except Exception as exc:
        session_outcome = "LOCK_PRECHECK_FAILED"
        session_reason = f"{type(exc).__name__}: {exc}"
        _fail("lock_precheck", session_reason)
        if output_path is not None:
            payload = _build_payload(
                trade_date=args.trade_date,
                execute_mode=args.execute,
                started_at=started_at,
                finished_at=datetime.now(KST).isoformat(),
                session_outcome=session_outcome,
                session_reason=session_reason,
                preopen_exit_code=None,
                preopen_result=None,
                polling_exit_code=None,
                polling_result=None,
                polling_started=False,
                polling_lock_name=polling_lock_name,
                output_path=output_path,
                buy_strategy=selection_to_buy_strategy(
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                ),
                run_timing1=run_timing1,
                run_timing2=run_timing2,
            )
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))
        return 5

    with tempfile.TemporaryDirectory(prefix="auto_trader_v2_session_") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        preopen_output_path = temp_dir / "preopen_result.json"
        polling_output_path = temp_dir / "polling_result.json"

        preopen_command = _build_preopen_command(
            args=args,
            db_path=str(db_path),
            output_path=preopen_output_path,
        )
        _section("Preopen Launch")
        _ok("script", "prepare_preopen_universe.py")
        preopen_exit_code = _run_child(preopen_command)
        preopen_result = _load_json(preopen_output_path)

        readiness_outcome = (
            None if preopen_result is None else preopen_result.get("readiness_outcome")
        )
        if preopen_exit_code != 0 or readiness_outcome != "READY":
            if preopen_exit_code == 4:
                session_outcome = "PREOPEN_BLOCKED"
            else:
                session_outcome = "PREOPEN_FAILED"
            session_reason = (
                None
                if preopen_result is None
                else (
                    preopen_result.get("readiness_reason")
                    or preopen_result.get("error_message")
                )
            )
            if session_reason is None:
                session_reason = (
                    "Preopen script did not finish with READY. "
                    f"exit_code={preopen_exit_code}, readiness_outcome={readiness_outcome}"
                )
        else:
            polling_command = _build_polling_command(
                args=args,
                db_path=str(db_path),
                output_path=polling_output_path,
            )
            _section("Polling Launch")
            _ok("script", "run_intraday_trading_polling.py")
            polling_started = True
            polling_exit_code = _run_child(polling_command)
            polling_result = _load_json(polling_output_path)

            if polling_exit_code == 0:
                session_outcome = "COMPLETED"
                session_reason = None
            elif polling_exit_code == 130:
                session_outcome = "POLLING_INTERRUPTED"
                session_reason = "Polling runner was interrupted."
            elif polling_exit_code == 4:
                session_outcome = "POLLING_BLOCKED"
                session_reason = (
                    None
                    if polling_result is None
                    else _optional_text(polling_result.get("stop_reason"))
                )
            else:
                session_outcome = "POLLING_FAILED"
                session_reason = (
                    None
                    if polling_result is None
                    else _optional_text(polling_result.get("stop_reason"))
                )
                if not session_reason:
                    session_reason = (
                        "Polling runner failed without structured result. "
                        f"exit_code={polling_exit_code}"
                    )

    _section("Session Result")
    _ok("session_outcome", session_outcome)
    if session_reason:
        _warn("session_reason", session_reason)
    _ok("preopen_exit_code", str(preopen_exit_code))
    _ok("polling_started", str(polling_started))
    _ok("polling_exit_code", str(polling_exit_code))

    if output_path is not None:
        payload = _build_payload(
            trade_date=args.trade_date,
            execute_mode=args.execute,
            started_at=started_at,
            finished_at=datetime.now(KST).isoformat(),
            session_outcome=session_outcome,
            session_reason=session_reason,
            preopen_exit_code=preopen_exit_code,
            preopen_result=preopen_result,
            polling_exit_code=polling_exit_code,
            polling_result=polling_result,
            polling_started=polling_started,
            polling_lock_name=polling_lock_name,
            output_path=output_path,
            buy_strategy=selection_to_buy_strategy(
                run_timing1=run_timing1,
                run_timing2=run_timing2,
            ),
            run_timing1=run_timing1,
            run_timing2=run_timing2,
        )
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    if session_outcome in ("COMPLETED",):
        return 0
    if session_outcome in ("PREOPEN_BLOCKED", "POLLING_BLOCKED", "POLLING_LOCK_BUSY"):
        return 4
    if session_outcome == "POLLING_INTERRUPTED":
        return 130
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
