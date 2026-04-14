"""
Phase 3-B-0 probe:
Create one mock fill intentionally, then capture raw VTTC8001R rows.

Purpose:
    - verify whether KIS daily filled-order rows contain:
        * stable execution identifier candidate
        * actual execution price candidate
        * usable execution timestamp fields
    - gather real raw samples before implementing ExecutionSyncService

Safety:
    - mock mode only
    - explicit --yes-mock-order gate required
    - POST is never retried
    - this script does not write to SQLite
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.kis import KisBroker
from broker.kis.auth import KisAuth
from broker.kis.client import KisClient
from broker.kis.endpoints import (
    PATH_INQUIRE_DAILY_CCLD,
    TR_ID_INQUIRE_DAILY_CCLD,
)
from broker.kis.errors import KisApiError, KisError, KisOrderError
from config.loader import load_settings
from logger import setup_logging

KST = pytz.timezone("Asia/Seoul")

KEY_PATTERNS = (
    "odno",
    "orgn",
    "ord",
    "ccld",
    "exec",
    "qty",
    "qnty",
    "pric",
    "unpr",
    "avg",
    "amt",
    "tm",
    "dt",
    "cncl",
    "pdno",
)


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
        description="Create one mock fill and capture raw VTTC8001R rows."
    )
    parser.add_argument(
        "--yes-mock-order",
        action="store_true",
        help="Actually place mock orders.",
    )
    parser.add_argument(
        "--code",
        default="005930",
        help="Stock code. Default: 005930",
    )
    parser.add_argument(
        "--qty",
        type=int,
        default=1,
        help="Quantity. Default: 1",
    )
    parser.add_argument(
        "--flatten-after-buy",
        action="store_true",
        help="After buy fill is confirmed, place one market sell to flatten.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=40.0,
        help="How long to wait for raw filled rows. Default: 40",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1.5,
        help="Polling interval for GET checks. Default: 1.5",
    )
    parser.add_argument(
        "--step-sleep-seconds",
        type=float,
        default=1.2,
        help="Safety sleep between major API steps. Default: 1.2",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path. Default: auto-generated under data/debug/",
    )
    return parser.parse_args()


def _today_yyyymmdd() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def _timestamp_slug() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _interesting_fields(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted(row.keys()):
        lowered = key.lower()
        if any(pattern in lowered for pattern in KEY_PATTERNS):
            value = row.get(key)
            if value not in (None, "", [], {}):
                result[key] = value
    return result


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _resolve_output_path(output_arg: str | None) -> Path:
    if output_arg:
        path = Path(output_arg)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    return (
        PROJECT_ROOT
        / "data"
        / "debug"
        / f"mock_fill_probe_{_timestamp_slug()}.json"
    ).resolve()


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=_json_default)


def _extract_output2_summary(body: dict[str, Any]) -> dict[str, Any]:
    output2 = body.get("output2", {})
    if not isinstance(output2, dict):
        return {
            "tot_ord_qty": 0,
            "tot_ccld_qty": 0,
            "tot_ccld_amt": 0,
            "avg_fill_price": 0,
            "raw": {},
        }

    tot_ord_qty = _to_int(output2.get("tot_ord_qty"))
    tot_ccld_qty = _to_int(output2.get("tot_ccld_qty"))
    tot_ccld_amt = _to_int(output2.get("tot_ccld_amt"))
    avg_fill_price = _to_int(output2.get("pchs_avg_pric"))

    if avg_fill_price == 0 and tot_ccld_qty > 0 and tot_ccld_amt > 0:
        avg_fill_price = (tot_ccld_amt + (tot_ccld_qty // 2)) // tot_ccld_qty

    return {
        "tot_ord_qty": tot_ord_qty,
        "tot_ccld_qty": tot_ccld_qty,
        "tot_ccld_amt": tot_ccld_amt,
        "avg_fill_price": avg_fill_price,
        "raw": dict(output2),
    }


def _summary_confirms_fill(summary: dict[str, Any], expected_min_qty: int) -> bool:
    return (
        int(summary.get("tot_ccld_qty", 0)) >= expected_min_qty
        and int(summary.get("tot_ccld_amt", 0)) > 0
    )


def _print_row_summary(label: str, rows: list[dict[str, Any]]) -> None:
    _section(label)
    if not rows:
        print("(no rows)")
        return

    sample = rows[0]
    print("interesting fields:")
    print(json.dumps(_interesting_fields(sample), ensure_ascii=False, indent=2))
    print()
    print("full first row:")
    print(json.dumps(sample, ensure_ascii=False, indent=2))


def _print_output2_summary(label: str, summary: dict[str, Any]) -> None:
    _section(label)
    print(
        json.dumps(
            {
                "tot_ord_qty": summary.get("tot_ord_qty", 0),
                "tot_ccld_qty": summary.get("tot_ccld_qty", 0),
                "tot_ccld_amt": summary.get("tot_ccld_amt", 0),
                "avg_fill_price": summary.get("avg_fill_price", 0),
                "raw": summary.get("raw", {}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _build_daily_ccld_params(
    account_no: str,
    start_date: str,
    end_date: str,
    *,
    code: str | None,
    order_no: str | None,
) -> dict[str, str]:
    cano, acnt_prdt_cd = account_no.split("-", 1)
    return {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "INQR_STRT_DT": start_date,
        "INQR_END_DT": end_date,
        "SLL_BUY_DVSN_CD": "00",
        "INQR_DVSN": "00",
        "PDNO": (code or "").strip(),
        "CCLD_DVSN": "00",
        "ORD_GNO_BRNO": "",
        "ODNO": (order_no or "").strip(),
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "0",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }


def _fetch_daily_ccld_probe(
    client: KisClient,
    *,
    account_no: str,
    code: str,
    order_no: str,
    start_date: str,
    end_date: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    params = _build_daily_ccld_params(
        account_no,
        start_date,
        end_date,
        code=code,
        order_no=order_no,
    )
    response = client.request_get(
        path=PATH_INQUIRE_DAILY_CCLD,
        tr_id=TR_ID_INQUIRE_DAILY_CCLD,
        params=params,
    )

    output1 = response.output1
    if output1 in (None, {}, []):
        rows: list[dict[str, Any]] = []
    elif not isinstance(output1, list):
        raise TypeError(f"output1 is not a list: {type(output1).__name__}")
    else:
        rows = [row for row in output1 if isinstance(row, dict)]

    body = response.body
    summary = _extract_output2_summary(body)
    return body, rows, summary


def _poll_daily_ccld_for_order(
    client: KisClient,
    *,
    account_no: str,
    code: str,
    order_no: str,
    expected_min_qty: int,
    timeout_seconds: float,
    poll_seconds: float,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    start_date = _today_yyyymmdd()
    end_date = start_date
    deadline = time.monotonic() + timeout_seconds

    last_body: dict[str, Any] | None = None
    last_rows: list[dict[str, Any]] = []
    last_summary: dict[str, Any] = {
        "tot_ord_qty": 0,
        "tot_ccld_qty": 0,
        "tot_ccld_amt": 0,
        "avg_fill_price": 0,
        "raw": {},
    }

    while time.monotonic() < deadline:
        body, rows, summary = _fetch_daily_ccld_probe(
            client,
            account_no=account_no,
            code=code,
            order_no=order_no,
            start_date=start_date,
            end_date=end_date,
        )
        last_body = body
        last_rows = rows
        last_summary = summary

        if rows or _summary_confirms_fill(summary, expected_min_qty):
            return last_body, last_rows, last_summary

        time.sleep(poll_seconds)

    return last_body, last_rows, last_summary


def _summarize_order_info(order_info: Any) -> str:
    return (
        f"status={getattr(order_info, 'status', None)} "
        f"order_no={getattr(order_info, 'order_no', None)} "
        f"qty={getattr(order_info, 'quantity', None)} "
        f"price={getattr(order_info, 'price', None)} "
        f"filled_qty={getattr(order_info, 'filled_qty', None)}"
    )


def main() -> int:
    args = _parse_args()

    if not args.yes_mock_order:
        _fail(
            "safety gate",
            "This script places mock orders. Re-run with --yes-mock-order.",
        )
        return 2

    if args.qty <= 0:
        _fail("input", f"--qty must be > 0: {args.qty!r}")
        return 2

    settings = load_settings()
    if settings.mode != "mock":
        _fail("mode check", f"mock only. current mode={settings.mode!r}")
        return 2

    setup_logging(settings)
    output_path = _resolve_output_path(args.output)

    _section("Phase 3-B-0 Mock Fill Probe")
    _ok("mode", settings.mode)
    _ok("code", args.code)
    _ok("qty", str(args.qty))
    _ok("flatten_after_buy", str(args.flatten_after_buy))
    _ok("output", str(output_path))

    auth = KisAuth(settings)
    raw_client = KisClient(settings, auth)

    payload: dict[str, Any] = {
        "started_at": datetime.now(KST).isoformat(),
        "mode": settings.mode,
        "code": args.code,
        "qty": args.qty,
        "flatten_after_buy": args.flatten_after_buy,
        "buy": {},
        "sell": {},
        "note": (
            "This script only reverses the share it buys in this run. "
            "If the mock account already held the same symbol beforehand, "
            "final position may still be non-zero."
        ),
    }

    try:
        with KisBroker(settings) as broker:
            _section("[1] Place BUY (market)")
            try:
                buy_info = broker.place_order(
                    code=args.code,
                    side="buy",
                    quantity=args.qty,
                    price=0,
                )
            except (KisApiError, KisOrderError, KisError) as exc:
                _fail("buy order", f"{type(exc).__name__}: {exc}")
                payload["buy"]["error"] = f"{type(exc).__name__}: {exc}"
                _save_json(output_path, payload)
                return 4

            _ok("buy accepted", _summarize_order_info(buy_info))
            payload["buy"]["order_info"] = {
                "order_no": buy_info.order_no,
                "status": buy_info.status.value,
                "quantity": buy_info.quantity,
                "price": buy_info.price,
                "filled_qty": buy_info.filled_qty,
                "timestamp": buy_info.timestamp.isoformat(),
                "raw_response": buy_info.raw_response,
            }

            if not buy_info.order_no:
                _fail("buy invariant", "accepted buy has no order_no")
                _save_json(output_path, payload)
                return 5

            time.sleep(args.step_sleep_seconds)

            _section("[2] Poll BUY daily_ccld")
            buy_body, buy_rows, buy_summary = _poll_daily_ccld_for_order(
                raw_client,
                account_no=settings.kis_account_no,
                code=args.code,
                order_no=buy_info.order_no,
                expected_min_qty=args.qty,
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
            )
            payload["buy"]["daily_ccld_body"] = buy_body
            payload["buy"]["daily_ccld_rows"] = buy_rows
            payload["buy"]["daily_ccld_summary"] = buy_summary

            buy_fill_confirmed = False
            if buy_rows:
                buy_fill_confirmed = True
                _ok("buy detail rows", f"count={len(buy_rows)}")
                _print_row_summary("BUY raw row summary", buy_rows)
            elif _summary_confirms_fill(buy_summary, args.qty):
                buy_fill_confirmed = True
                _warn(
                    "buy detail rows",
                    "output1 is empty, but output2 summary confirms fill.",
                )
                _print_output2_summary("BUY output2 summary", buy_summary)

            if not buy_fill_confirmed:
                _warn(
                    "buy fill probe",
                    "Neither output1 rows nor output2 summary confirmed fill. "
                    "Saved last body for inspection.",
                )
                _save_json(output_path, payload)
                return 4

            if not args.flatten_after_buy:
                _save_json(output_path, payload)
                _ok("saved", str(output_path))
                _warn(
                    "position note",
                    "BUY was confirmed, but flatten_after_buy=False. "
                    "Mock account may now hold this symbol.",
                )
                return 0

            time.sleep(args.step_sleep_seconds)

            _section("[3] Place SELL (market)")
            try:
                sell_info = broker.place_order(
                    code=args.code,
                    side="sell",
                    quantity=args.qty,
                    price=0,
                )
            except (KisApiError, KisOrderError, KisError) as exc:
                _fail("sell order", f"{type(exc).__name__}: {exc}")
                payload["sell"]["error"] = f"{type(exc).__name__}: {exc}"
                _save_json(output_path, payload)
                return 4

            _ok("sell accepted", _summarize_order_info(sell_info))
            payload["sell"]["order_info"] = {
                "order_no": sell_info.order_no,
                "status": sell_info.status.value,
                "quantity": sell_info.quantity,
                "price": sell_info.price,
                "filled_qty": sell_info.filled_qty,
                "timestamp": sell_info.timestamp.isoformat(),
                "raw_response": sell_info.raw_response,
            }

            if not sell_info.order_no:
                _fail("sell invariant", "accepted sell has no order_no")
                _save_json(output_path, payload)
                return 5

            time.sleep(args.step_sleep_seconds)

            _section("[4] Poll SELL daily_ccld")
            sell_body, sell_rows, sell_summary = _poll_daily_ccld_for_order(
                raw_client,
                account_no=settings.kis_account_no,
                code=args.code,
                order_no=sell_info.order_no,
                expected_min_qty=args.qty,
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
            )
            payload["sell"]["daily_ccld_body"] = sell_body
            payload["sell"]["daily_ccld_rows"] = sell_rows
            payload["sell"]["daily_ccld_summary"] = sell_summary

            sell_fill_confirmed = False
            if sell_rows:
                sell_fill_confirmed = True
                _ok("sell detail rows", f"count={len(sell_rows)}")
                _print_row_summary("SELL raw row summary", sell_rows)
            elif _summary_confirms_fill(sell_summary, args.qty):
                sell_fill_confirmed = True
                _warn(
                    "sell detail rows",
                    "output1 is empty, but output2 summary confirms fill.",
                )
                _print_output2_summary("SELL output2 summary", sell_summary)

            _save_json(output_path, payload)

            _section("[5] Summary")
            _ok("saved", str(output_path))

            if sell_fill_confirmed:
                _ok(
                    "probe complete",
                    "BUY/SELL fill confirmation captured. "
                    "If output1 stays empty, Phase 3-B must support "
                    "mock output2-summary fallback.",
                )
                return 0

            _warn(
                "probe incomplete",
                "SELL fill was not confirmed. Check mock balance manually.",
            )
            return 4

    finally:
        raw_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
