"""
Phase 3-A-4 mock KIS end-to-end script.

Scenario:
    1. current price lookup
    2. place one mock buy order through OrderService
    3. cancel the same order through OrderService
    4. re-check DB row and today's fill inquiry result

Safety:
    - Runs only in mock mode
    - Requires explicit --yes-mock-order flag
    - Does not retry POST requests
    - Uses only client_order_id for cancellation
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CODE = "005930"
DEFAULT_QTY = 1
DEFAULT_DISCOUNT_BPS = 500
DEFAULT_SLEEP_SECONDS = 1.5
DEFAULT_STRATEGY_NAME = "phase3a4"


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 3-A-4 mock KIS end-to-end test script."
    )
    parser.add_argument(
        "--yes-mock-order",
        action="store_true",
        help="Actually place a mock order and continue the flow.",
    )
    parser.add_argument(
        "--code",
        default=DEFAULT_CODE,
        help=f"6-digit stock code. Default: {DEFAULT_CODE}",
    )
    parser.add_argument(
        "--qty",
        type=int,
        default=DEFAULT_QTY,
        help=f"Order quantity. Default: {DEFAULT_QTY}",
    )
    parser.add_argument(
        "--limit-price",
        type=int,
        default=None,
        help="Explicit limit price. If omitted, auto-calc is allowed only for 005930.",
    )
    parser.add_argument(
        "--discount-bps",
        type=int,
        default=DEFAULT_DISCOUNT_BPS,
        help=f"Auto price discount in bps for 005930 only. Default: {DEFAULT_DISCOUNT_BPS}",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Delay between broker calls. Default: {DEFAULT_SLEEP_SECONDS}",
    )
    parser.add_argument(
        "--strategy-name",
        default=DEFAULT_STRATEGY_NAME,
        help=f"Strategy name recorded in client_order_id. Default: {DEFAULT_STRATEGY_NAME}",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    return parser.parse_args()


def _resolve_limit_price(
    *,
    code: str,
    current_price: int,
    explicit_price: int | None,
    discount_bps: int,
) -> int:
    if explicit_price is not None:
        if explicit_price <= 0:
            raise ValueError(f"--limit-price must be > 0: {explicit_price!r}")
        return explicit_price

    if code != DEFAULT_CODE:
        raise ValueError(
            f"Auto limit price is supported only for {DEFAULT_CODE}. "
            f"For code={code}, please pass --limit-price explicitly."
        )

    if discount_bps < 1 or discount_bps >= 10_000:
        raise ValueError(
            f"--discount-bps must be between 1 and 9999: {discount_bps!r}"
        )

    discounted = current_price * (10_000 - discount_bps) // 10_000
    if discounted <= 0:
        raise ValueError(
            f"Calculated limit price is invalid: current={current_price}, "
            f"discount_bps={discount_bps}"
        )

    # Default script is tuned for Samsung Electronics (005930),
    # whose tick size is 100 KRW around the typical trading range.
    rounded = max(100, (discounted // 100) * 100)
    return rounded


def _describe_order_row(row) -> str:
    return (
        f"status={row.status.value} "
        f"kis_order_no={row.kis_order_no} "
        f"filled_qty={row.filled_qty} "
        f"avg_fill_price={row.avg_fill_price} "
        f"closed_at={row.closed_at} "
        f"error_code={row.error_code}"
    )


def _describe_place_result(result) -> str:
    return (
        f"outcome={result.outcome.value} "
        f"client_order_id={result.client_order_id} "
        f"error_code={result.error_code} "
        f"error_message={result.error_message}"
    )


def _describe_cancel_result(result) -> str:
    status_text = None
    if result.order_row is not None:
        status_text = result.order_row.status.value

    return (
        f"outcome={result.outcome.value} "
        f"client_order_id={result.client_order_id} "
        f"db_status={status_text} "
        f"error_code={result.error_code} "
        f"error_message={result.error_message}"
    )


def main() -> int:
    args = _parse_args()

    if not args.yes_mock_order:
        _fail(
            "safety gate",
            "This script places a mock order. Re-run with --yes-mock-order.",
        )
        return 2

    from broker.kis import KisBroker
    from broker.kis.errors import KisApiError
    from config.loader import load_settings
    from logger import setup_logging
    from services import CancelOutcome, OrderOutcome, OrderService
    from storage.db import get_connection
    from storage.migrations.runner import run_migrations
    from storage.repositories import DbOrderStatus, OrderRepository, PositionRepository

    _section("Phase 3-A-4 Mock End-to-End")

    try:
        settings = load_settings()
    except Exception as exc:
        _fail("load_settings", f"{type(exc).__name__}: {exc}")
        return 5

    if settings.mode != "mock":
        _fail("mode check", f"mock only. current mode={settings.mode!r}")
        return 2

    setup_logging(settings)

    db_path = args.db_path or settings.db_path
    _ok("mode check", "mock")
    _ok("db path", str(db_path))

    try:
        run_migrations(db_path)
    except Exception as exc:
        _fail("run_migrations", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("get_connection", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        with KisBroker(settings) as broker:
            service = OrderService(
                broker=broker,
                conn=conn,
                order_repo=order_repo,
                position_repo=position_repo,
            )

            _section("[1] Current Price")
            try:
                snapshot = broker.get_current_price(args.code)
            except Exception as exc:
                _fail("get_current_price", f"{type(exc).__name__}: {exc}")
                return 5

            try:
                limit_price = _resolve_limit_price(
                    code=args.code,
                    current_price=snapshot.price,
                    explicit_price=args.limit_price,
                    discount_bps=args.discount_bps,
                )
            except Exception as exc:
                _fail("resolve_limit_price", f"{type(exc).__name__}: {exc}")
                return 5

            _ok(
                "price ready",
                f"code={args.code} current={snapshot.price:,} "
                f"limit={limit_price:,} qty={args.qty}",
            )

            time.sleep(args.sleep_seconds)

            _section("[2] place_order")
            try:
                place_result = service.place_order(
                    symbol=args.code,
                    side="buy",
                    qty=args.qty,
                    price=limit_price,
                    order_type="LIMIT",
                    strategy_name=args.strategy_name,
                )
            except Exception as exc:
                _fail("place_order", f"{type(exc).__name__}: {exc}")
                return 5

            _ok("place result", _describe_place_result(place_result))

            db_after_place = order_repo.get_by_client_order_id(
                place_result.client_order_id
            )
            if db_after_place is None:
                _fail(
                    "db after place",
                    f"row not found: client_order_id={place_result.client_order_id}",
                )
                return 5

            _ok("db after place", _describe_order_row(db_after_place))

            if place_result.outcome != OrderOutcome.SUBMITTED:
                _warn(
                    "stop",
                    "Order was not SUBMITTED, so cancel step is skipped.",
                )
                return 3

            order_no = db_after_place.kis_order_no
            if not order_no:
                _fail("db invariant", "SUBMITTED order has no kis_order_no")
                return 5

            time.sleep(args.sleep_seconds)

            _section("[3] cancel_order")
            try:
                cancel_result = service.cancel_order(
                    client_order_id=place_result.client_order_id
                )
            except Exception as exc:
                _fail("cancel_order", f"{type(exc).__name__}: {exc}")
                return 5

            _ok("cancel result", _describe_cancel_result(cancel_result))

            db_after_cancel = order_repo.get_by_client_order_id(
                place_result.client_order_id
            )
            if db_after_cancel is None:
                _fail(
                    "db after cancel",
                    f"row not found: client_order_id={place_result.client_order_id}",
                )
                return 5

            _ok("db after cancel", _describe_order_row(db_after_cancel))

            time.sleep(args.sleep_seconds)

            _section("[4] Broker Fill Inquiry")
            fills = []
            try:
                fills = broker.get_order_status(
                    order_no=order_no,
                    filled_only=True,
                )
            except KisApiError as exc:
                _warn("filled inquiry", str(exc))
            except Exception as exc:
                _warn("filled inquiry", f"{type(exc).__name__}: {exc}")
            else:
                if fills:
                    latest = fills[-1]
                    _warn(
                        "filled rows found",
                        f"count={len(fills)} "
                        f"latest_status={latest.status.value} "
                        f"latest_filled_qty={latest.filled_qty}",
                    )
                else:
                    _ok("filled rows", "No fill rows found for this order_no.")

            _section("[5] Summary")
            if (
                cancel_result.outcome == CancelOutcome.CANCELLED
                and db_after_cancel.status == DbOrderStatus.CANCELLED
            ):
                _ok(
                    "phase 3-a-4",
                    f"SUBMITTED -> CANCELLED confirmed. "
                    f"client_order_id={place_result.client_order_id}",
                )
                return 0

            if cancel_result.outcome == CancelOutcome.REJECTED:
                _warn(
                    "phase 3-a-4",
                    "Cancel was rejected. The order may already have filled. "
                    "Try a lower --limit-price and re-run.",
                )
                return 4

            if cancel_result.outcome == CancelOutcome.UNKNOWN:
                _warn(
                    "phase 3-a-4",
                    "Cancel outcome is UNKNOWN. Do not retry POST blindly. "
                    "Check logs, HTS, and fill inquiry first.",
                )
                return 4

            _warn(
                "phase 3-a-4",
                f"Expected CANCELLED but got {cancel_result.outcome.value}.",
            )
            return 4

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
