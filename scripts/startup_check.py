"""
Manual startup safety check.

Runs universe snapshot check first, then reconciliation,
then returns READY/BLOCKED for trading startup.
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
from services import StartupOutcome, StartupService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    OrderRepository,
    PositionRepository,
    UniverseCandidateRepository,
)

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
    parser = argparse.ArgumentParser(description="Run startup safety check.")
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--allow-unresolved-orders",
        action="store_true",
        help="Override safety gate and continue even if unresolved orders exist.",
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


def _resolve_output_path(output_arg: str | None) -> Path | None:
    if not output_arg:
        return None
    path = Path(output_arg)
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
    output_path = _resolve_output_path(args.output)

    _section("Startup Check")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("db_path", str(db_path))
    _ok("allow_unresolved_orders", str(args.allow_unresolved_orders))

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
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        universe_repo = UniverseCandidateRepository(conn)

        with KisBroker(settings) as broker:
            service = StartupService(
                broker=broker,
                conn=conn,
                order_repo=order_repo,
                position_repo=position_repo,
                universe_repo=universe_repo,
            )
            result = service.run_startup_check(
                trade_date=args.trade_date,
                allow_unresolved_orders=args.allow_unresolved_orders,
            )

        _section("Startup Result")
        _ok("outcome", result.outcome.value)
        _ok("checked_at", result.checked_at)
        _ok("trade_date", result.trade_date)
        _ok("universe_exists", str(result.universe_snapshot.exists))
        _ok("universe_candidate_count", str(result.universe_snapshot.candidate_count))
        _ok("universe_refreshed_at", str(result.universe_snapshot.refreshed_at))
        _ok(
            "reconcile_changed_rows",
            (
                "not_run"
                if result.reconcile_result is None
                else str(result.reconcile_result.changed_rows)
            ),
        )
        _ok(
            "unresolved_orders",
            (
                "0"
                if result.reconcile_result is None
                else str(len(result.reconcile_result.unresolved_orders))
            ),
        )
        _ok("live_positions", str(len(result.live_positions)))

        if result.reason:
            _warn("reason", result.reason)

        if result.live_positions:
            _section("Live Positions")
            for row in result.live_positions:
                print(
                    f"{row.symbol} qty={row.qty} avg_price={row.avg_price} "
                    f"updated_at={row.updated_at}"
                )

        if output_path is not None:
            payload = {
                "outcome": result.outcome.value,
                "checked_at": result.checked_at,
                "trade_date": result.trade_date,
                "reason": result.reason,
                "universe_snapshot": {
                    "exists": result.universe_snapshot.exists,
                    "candidate_count": result.universe_snapshot.candidate_count,
                    "refreshed_at": result.universe_snapshot.refreshed_at,
                },
                "reconcile_changed_rows": (
                    None
                    if result.reconcile_result is None
                    else result.reconcile_result.changed_rows
                ),
                "unresolved_orders": (
                    []
                    if result.reconcile_result is None
                    else [
                        {
                            "client_order_id": row.client_order_id,
                            "status": row.status.value,
                            "symbol": row.symbol,
                            "side": row.side,
                            "qty": row.qty,
                            "kis_order_no": row.kis_order_no,
                        }
                        for row in result.reconcile_result.unresolved_orders
                    ]
                ),
                "live_positions": [
                    {
                        "symbol": row.symbol,
                        "qty": row.qty,
                        "avg_price": row.avg_price,
                        "updated_at": row.updated_at,
                    }
                    for row in result.live_positions
                ],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0 if result.outcome == StartupOutcome.READY else 4

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
