"""
Preview manual execution-recovery review items.

Flow:
1. safely sync unresolved orders from the broker
2. preview what can be auto-finalized from existing local execution rows
3. build a review report only for the orders that still need manual recovery

Safety:
- read-only preview only
- no DB mutations are performed
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
from services import (
    ExecutionRecoveryFinalizeService,
    ManualExecutionRecoveryReviewService,
    UnresolvedOrderSyncService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
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
    parser = argparse.ArgumentParser(
        description="Preview review items for manual execution recovery."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many review rows to print. Default: 20",
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


def _build_payload(*, trade_date: str, result=None, error_type=None, error_message=None):
    payload = {
        "trade_date": trade_date,
        "error_type": error_type,
        "error_message": error_message,
        "result": None,
    }
    if result is None:
        return payload

    payload["result"] = {
        "trade_date": result.trade_date,
        "review_item_count": result.review_item_count,
        "recovery_result": {
            "candidate_count": result.recovery_result.candidate_count,
            "preview_ready_count": result.recovery_result.preview_ready_count,
            "recovered_count": result.recovery_result.recovered_count,
            "manual_recovery_required_count": (
                result.recovery_result.manual_recovery_required_count
            ),
            "skipped_count": result.recovery_result.skipped_count,
        },
        "items": [
            {
                "client_order_id": item.client_order_id,
                "symbol": item.symbol,
                "side": item.side,
                "order_qty": item.order_qty,
                "order_price": item.order_price,
                "order_type": item.order_type,
                "order_status": item.order_status,
                "kis_order_no": item.kis_order_no,
                "requested_at": item.requested_at,
                "submitted_at": item.submitted_at,
                "closed_at": item.closed_at,
                "broker_status": item.broker_status,
                "broker_filled_qty": item.broker_filled_qty,
                "local_execution_count": item.local_execution_count,
                "local_filled_qty": item.local_filled_qty,
                "local_avg_fill_price": item.local_avg_fill_price,
                "current_position_qty": item.current_position_qty,
                "current_position_avg_price": item.current_position_avg_price,
                "recommendation": item.recommendation.value,
                "reason_code": item.reason_code,
                "reason_message": item.reason_message,
                "executions": [
                    {
                        "kis_exec_no": row.kis_exec_no,
                        "qty": row.qty,
                        "price": row.price,
                        "executed_at": row.executed_at,
                    }
                    for row in item.executions
                ],
            }
            for item in result.items
        ],
    }
    return payload


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

    _section("Review Execution Recovery")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("db_path", str(db_path))

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("db setup", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ),
            )
        return 5

    try:
        with KisBroker(settings) as broker:
            order_repo = OrderRepository(conn)
            execution_repo = ExecutionRepository(conn)
            position_repo = PositionRepository(conn)
            sync_service = UnresolvedOrderSyncService(
                broker=broker,
                conn=conn,
                order_repo=order_repo,
            )
            execution_recovery_service = ExecutionRecoveryFinalizeService(
                conn=conn,
                order_repo=order_repo,
                execution_repo=execution_repo,
                sync_service=sync_service,
            )
            review_service = ManualExecutionRecoveryReviewService(
                order_repo=order_repo,
                execution_repo=execution_repo,
                position_repo=position_repo,
                execution_recovery_service=execution_recovery_service,
            )
            result = review_service.build_review(trade_date=args.trade_date)

        _section("Review Result")
        _ok("review_item_count", str(result.review_item_count))
        _ok(
            "initial_recovery_candidate_count",
            str(result.recovery_result.candidate_count),
        )
        _ok(
            "manual_recovery_required_count",
            str(result.recovery_result.manual_recovery_required_count),
        )
        _ok(
            "auto_recoverable_count",
            str(result.recovery_result.preview_ready_count),
        )

        visible_items = result.items[: max(0, args.limit)]
        if visible_items:
            _section("Items")
            for item in visible_items:
                print(
                    f"{item.client_order_id} symbol={item.symbol} "
                    f"status={item.order_status} broker_status={item.broker_status or '-'} "
                    f"broker_filled_qty={item.broker_filled_qty if item.broker_filled_qty is not None else '-'} "
                    f"local_filled_qty={item.local_filled_qty} "
                    f"recommendation={item.recommendation.value} "
                    f"reason={item.reason_code or '-'}"
                )
        else:
            _warn("items", "No manual recovery review items were found.")

        if output_path is not None:
            _save_json(
                output_path,
                _build_payload(trade_date=args.trade_date, result=result),
            )
            _ok("json_saved", str(output_path))

        return 0

    except Exception as exc:
        _fail("review", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ),
            )
        return 5

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
