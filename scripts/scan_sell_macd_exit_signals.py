"""
Scan persisted completed 15-minute bars for sell MACD decrease signals.

Scope:
- read-only strategy scan
- no order placement
- optional signal DB recording with --write
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging
from services import SellMacdExitScanService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    IntradayBar15mRepository,
    PositionRepository,
    SignalRepository,
)
from strategy import SellMacdExitSettings

KST = pytz.timezone("Asia/Seoul")


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
        description="Scan completed 15-minute bars for sell MACD decrease signals."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--fast-window",
        type=int,
        default=12,
        help="MACD fast EMA window. Default: 12",
    )
    parser.add_argument(
        "--slow-window",
        type=int,
        default=26,
        help="MACD slow EMA window. Default: 26",
    )
    parser.add_argument(
        "--signal-window",
        type=int,
        default=9,
        help="MACD signal EMA window. Default: 9",
    )
    parser.add_argument(
        "--consecutive-decline-bars",
        type=int,
        default=2,
        help="How many consecutive histogram decreases to require. Default: 2",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=300,
        help="How many persisted 15-minute bars to inspect. Default: 300",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually record signals into SQLite.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many matched rows to print. Default: 20",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path.",
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


def main() -> int:
    args = _parse_args()

    try:
        settings = load_settings()
        setup_logging(settings)
        strategy_settings = SellMacdExitSettings(
            fast_window=args.fast_window,
            slow_window=args.slow_window,
            signal_window=args.signal_window,
            consecutive_decline_bars=args.consecutive_decline_bars,
        ).validated()
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None

    _section("Scan Sell MACD Exit Signals")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("fast_window", str(strategy_settings.fast_window))
    _ok("slow_window", str(strategy_settings.slow_window))
    _ok("signal_window", str(strategy_settings.signal_window))
    _ok(
        "consecutive_decline_bars",
        str(strategy_settings.consecutive_decline_bars),
    )
    _ok("history_limit", str(args.history_limit))
    _ok("write", str(args.write))
    _ok("db_path", str(db_path))

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("db setup", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        service = SellMacdExitScanService(
            conn=conn,
            position_repo=PositionRepository(conn),
            intraday_bar_repo=IntradayBar15mRepository(conn),
            signal_repo=SignalRepository(conn),
        )
        result = service.scan(
            trade_date=args.trade_date,
            settings=strategy_settings,
            history_limit=args.history_limit,
            write_signals=args.write,
        )

        _section("Scan Result")
        _ok("position_count", str(result.position_count))
        _ok("matched_count", str(result.matched_count))
        _ok("recorded_count", str(result.recorded_count))
        _ok("skipped_existing_count", str(result.skipped_existing_count))
        _ok("scanned_at", result.scanned_at)

        visible_candidates = result.candidates[: max(0, args.limit)]
        if visible_candidates:
            _section("Matched")
            for candidate in visible_candidates:
                print(
                    f"{candidate.symbol} qty={candidate.qty} "
                    f"avg_price={candidate.avg_price} "
                    f"bar_end_at={candidate.match.bar_end_at} "
                    f"hist_t_minus_2={candidate.match.hist_t_minus_2:.6f} "
                    f"hist_t_minus_1={candidate.match.hist_t_minus_1:.6f} "
                    f"hist_t={candidate.match.hist_t:.6f} "
                    f"already_recorded={candidate.already_recorded}"
                )
        else:
            _warn("matched", "No sell MACD exit matches found.")

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "scanned_at": result.scanned_at,
                "position_count": result.position_count,
                "matched_count": result.matched_count,
                "recorded_count": result.recorded_count,
                "skipped_existing_count": result.skipped_existing_count,
                "write_requested": args.write,
                "candidates": [
                    {
                        "symbol": candidate.symbol,
                        "name": candidate.name,
                        "qty": candidate.qty,
                        "avg_price": candidate.avg_price,
                        "history_bar_count": candidate.history_bar_count,
                        "already_recorded": candidate.already_recorded,
                        "bar_start_at": candidate.match.bar_start_at,
                        "bar_end_at": candidate.match.bar_end_at,
                        "close_price": candidate.match.close_price,
                        "macd_value": round(candidate.match.macd_value, 6),
                        "signal_value": round(candidate.match.signal_value, 6),
                        "hist_t_minus_2": round(candidate.match.hist_t_minus_2, 6),
                        "hist_t_minus_1": round(candidate.match.hist_t_minus_1, 6),
                        "hist_t": round(candidate.match.hist_t, 6),
                        "consecutive_decline_bars": (
                            candidate.match.consecutive_decline_bars
                        ),
                    }
                    for candidate in result.candidates
                ],
                "recorded_signal_ids": [row.id for row in result.recorded_signals],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0

    except Exception as exc:
        _fail("scan", f"{type(exc).__name__}: {exc}")
        return 5

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
