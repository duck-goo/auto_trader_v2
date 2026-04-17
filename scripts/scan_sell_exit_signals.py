"""
Scan live positions for sell stop-loss / take-profit signals.

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

from broker.kis import KisBroker
from config.loader import load_settings
from logger import setup_logging
from services import SellExitScanService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import PositionRepository, SignalRepository
from strategy import SellExitSettings

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
        description="Scan live positions for sell stop-loss / take-profit signals."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--stop-loss-percent",
        type=float,
        default=3.0,
        help="Stop-loss percent. Example: 3 means 3 percent. Default: 3",
    )
    parser.add_argument(
        "--take-profit-percent",
        type=float,
        default=5.0,
        help="Take-profit percent. Example: 5 means 5 percent. Default: 5",
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
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None

    strategy_settings = SellExitSettings(
        stop_loss_ratio=args.stop_loss_percent / 100.0,
        take_profit_ratio=args.take_profit_percent / 100.0,
    )

    _section("Scan Sell Exit Signals")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("stop_loss_percent", str(args.stop_loss_percent))
    _ok("take_profit_percent", str(args.take_profit_percent))
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
        with KisBroker(settings) as broker:
            service = SellExitScanService(
                broker=broker,
                conn=conn,
                position_repo=PositionRepository(conn),
                signal_repo=SignalRepository(conn),
            )
            result = service.scan(
                trade_date=args.trade_date,
                settings=strategy_settings,
                write_signals=args.write,
            )

        _section("Scan Result")
        _ok("position_count", str(result.position_count))
        _ok("matched_count", str(result.matched_count))
        _ok("stop_loss_count", str(result.stop_loss_count))
        _ok("take_profit_count", str(result.take_profit_count))
        _ok("recorded_count", str(result.recorded_count))
        _ok("skipped_existing_count", str(result.skipped_existing_count))
        _ok("scanned_at", result.scanned_at)

        visible_candidates = result.candidates[: max(0, args.limit)]
        if visible_candidates:
            _section("Matched")
            for candidate in visible_candidates:
                print(
                    f"{candidate.symbol} name={candidate.name} "
                    f"qty={candidate.qty} avg_price={candidate.avg_price} "
                    f"current_price={candidate.current_price} "
                    f"rule={candidate.match.rule.value} "
                    f"trigger_price={candidate.match.trigger_price} "
                    f"already_recorded={candidate.already_recorded}"
                )
        else:
            _warn("matched", "No sell exit matches found.")

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "scanned_at": result.scanned_at,
                "position_count": result.position_count,
                "matched_count": result.matched_count,
                "stop_loss_count": result.stop_loss_count,
                "take_profit_count": result.take_profit_count,
                "recorded_count": result.recorded_count,
                "skipped_existing_count": result.skipped_existing_count,
                "write_requested": args.write,
                "candidates": [
                    {
                        "symbol": candidate.symbol,
                        "name": candidate.name,
                        "qty": candidate.qty,
                        "avg_price": candidate.avg_price,
                        "current_price": candidate.current_price,
                        "strategy_name": candidate.strategy_name,
                        "already_recorded": candidate.already_recorded,
                        "rule": candidate.match.rule.value,
                        "trigger_price": candidate.match.trigger_price,
                        "stop_loss_ratio": round(
                            candidate.match.stop_loss_ratio,
                            6,
                        ),
                        "take_profit_ratio": round(
                            candidate.match.take_profit_ratio,
                            6,
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
