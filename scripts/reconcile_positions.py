"""
Manual position reconciliation runner.

Purpose:
    Compare local positions against broker balance snapshot,
    then reconcile local positions safely.

Safety:
    - default: block when unresolved orders exist
    - block when a position diff would touch a symbol with an open entry lot
    - broker API is called outside DB transaction
    - only positions are changed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.kis import KisBroker
from config.loader import load_settings
from logger import setup_logging
from services import ReconcileOutcome, ReconcileService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import EntryLotRepository, OrderRepository, PositionRepository


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
        description="Run manual position reconciliation."
    )
    parser.add_argument(
        "--allow-unresolved-orders",
        action="store_true",
        help="Override safety gate and reconcile even if unresolved orders exist.",
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


def _position_rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row.symbol,
            "qty": row.qty,
            "avg_price": row.avg_price,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


def _unresolved_orders_to_dicts(rows) -> list[dict[str, Any]]:
    return [
        {
            "client_order_id": row.client_order_id,
            "kis_order_no": row.kis_order_no,
            "symbol": row.symbol,
            "side": row.side,
            "qty": row.qty,
            "price": row.price,
            "status": row.status.value,
            "requested_at": row.requested_at,
            "submitted_at": row.submitted_at,
            "closed_at": row.closed_at,
        }
        for row in rows
    ]


def _diffs_to_dicts(diffs) -> list[dict[str, Any]]:
    return [
        {
            "symbol": diff.symbol,
            "action": diff.action.value,
            "local_qty": diff.local_qty,
            "local_avg_price": diff.local_avg_price,
            "broker_qty": diff.broker_qty,
            "broker_avg_price": diff.broker_avg_price,
        }
        for diff in diffs
    ]


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

    _section("Reconcile Positions")
    _ok("mode", settings.mode)
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

        before_rows = position_repo.list_all_including_zero()

        _section("Local Positions Before")
        if before_rows:
            for row in before_rows:
                print(
                    f"{row.symbol} qty={row.qty} avg_price={row.avg_price} "
                    f"updated_at={row.updated_at}"
                )
        else:
            print("(no local position rows)")

        with KisBroker(settings) as broker:
            service = ReconcileService(
                broker=broker,
                conn=conn,
                order_repo=order_repo,
                position_repo=position_repo,
                entry_lot_repo=EntryLotRepository(conn),
            )
            result = service.reconcile_positions(
                allow_unresolved_orders=args.allow_unresolved_orders
            )

        _section("Reconcile Result")
        _ok("outcome", result.outcome.value)
        _ok("changed_rows", str(result.changed_rows))
        _ok("unresolved_orders", str(len(result.unresolved_orders)))
        _ok("reconciled_at", result.reconciled_at)

        if result.outcome == ReconcileOutcome.BLOCKED:
            _warn(
                "blocked",
                result.reason_message
                or "Position reconciliation was blocked.",
            )

        if result.unresolved_orders:
            _section("Unresolved Orders")
            for row in result.unresolved_orders:
                print(
                    f"{row.client_order_id} status={row.status.value} "
                    f"symbol={row.symbol} side={row.side} qty={row.qty} "
                    f"kis_order_no={row.kis_order_no}"
                )

        if result.diffs:
            _section("Applied Diffs")
            for diff in result.diffs:
                print(
                    f"{diff.symbol} action={diff.action.value} "
                    f"local=({diff.local_qty}, {diff.local_avg_price}) "
                    f"broker=({diff.broker_qty}, {diff.broker_avg_price})"
                )
        else:
            _section("Applied Diffs")
            print("(no changes)")

        after_rows = position_repo.list_all_including_zero()
        _section("Local Positions After")
        if after_rows:
            for row in after_rows:
                print(
                    f"{row.symbol} qty={row.qty} avg_price={row.avg_price} "
                    f"updated_at={row.updated_at}"
                )
        else:
            print("(no local position rows)")

        if output_path is not None:
            payload = {
                "mode": settings.mode,
                "db_path": str(db_path),
                "allow_unresolved_orders": args.allow_unresolved_orders,
                "result": {
                    "outcome": result.outcome.value,
                    "changed_rows": result.changed_rows,
                    "reconciled_at": result.reconciled_at,
                    "reason_code": result.reason_code,
                    "reason_message": result.reason_message,
                    "diffs": _diffs_to_dicts(result.diffs),
                    "unresolved_orders": _unresolved_orders_to_dicts(
                        result.unresolved_orders
                    ),
                },
                "positions_before": _position_rows_to_dicts(before_rows),
                "positions_after": _position_rows_to_dicts(after_rows),
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
