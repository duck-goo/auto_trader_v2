"""
Read-only probe for KIS inquire-daily-ccld response.

Purpose:
    Verify whether raw filled-order rows expose enough fields to build
    ExecutionSyncService safely:
        - stable execution identifier
        - actual execution price
        - reliable executed timestamp
        - per-row meaning (execution event vs order snapshot)

Safety:
    - GET only
    - no order placement
    - no cancellation
    - no DB writes
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.kis.auth import KisAuth
from broker.kis.client import KisClient
from broker.kis.endpoints import (
    PATH_INQUIRE_DAILY_CCLD,
    TR_ID_INQUIRE_DAILY_CCLD,
)
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
)


def _yyyymmdd(value: str) -> str:
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Date must be YYYYMMDD: {value!r}"
        ) from exc
    return value


def _parse_args() -> argparse.Namespace:
    today = datetime.now(KST).strftime("%Y%m%d")

    parser = argparse.ArgumentParser(
        description="Inspect raw KIS daily filled-order rows safely."
    )
    parser.add_argument(
        "--start-date",
        type=_yyyymmdd,
        default=today,
        help=f"Start date YYYYMMDD. Default: {today}",
    )
    parser.add_argument(
        "--end-date",
        type=_yyyymmdd,
        default=today,
        help=f"End date YYYYMMDD. Default: {today}",
    )
    parser.add_argument(
        "--order-no",
        default=None,
        help="Optional KIS order_no filter (ODNO).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="How many sample rows to print. Default: 3",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path for the raw response/body.",
    )
    return parser.parse_args()


def _non_empty(value: object) -> bool:
    return value not in (None, "", [], {})


def _interesting_keys(keys: set[str]) -> list[str]:
    result = []
    for key in sorted(keys):
        lowered = key.lower()
        if any(pattern in lowered for pattern in KEY_PATTERNS):
            result.append(key)
    return result


def _interesting_fields(row: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in sorted(row.keys()):
        lowered = key.lower()
        if any(pattern in lowered for pattern in KEY_PATTERNS):
            value = row.get(key)
            if _non_empty(value):
                result[key] = value
    return result


def _print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _save_json(path_text: str, payload: dict[str, object]) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def main() -> int:
    args = _parse_args()

    if args.end_date < args.start_date:
        print(
            f"[FAIL] end-date must be >= start-date: "
            f"{args.start_date} .. {args.end_date}"
        )
        return 2

    settings = load_settings()
    setup_logging(settings)

    auth = KisAuth(settings)
    client = KisClient(settings, auth)

    try:
        params = {
            "CANO": settings.kis_account_no.split("-")[0],
            "ACNT_PRDT_CD": settings.kis_account_no.split("-")[1],
            "INQR_STRT_DT": args.start_date,
            "INQR_END_DT": args.end_date,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "0",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        response = client.request_get(
            path=PATH_INQUIRE_DAILY_CCLD,
            tr_id=TR_ID_INQUIRE_DAILY_CCLD,
            params=params,
        )
    except Exception as exc:
        print(f"[FAIL] request_get: {type(exc).__name__}: {exc}")
        return 5
    finally:
        client.close()

    output1 = response.output1
    if output1 in (None, {}, []):
        rows: list[dict[str, object]] = []
    elif not isinstance(output1, list):
        print(
            f"[FAIL] output1 is not a list: {type(output1).__name__}"
        )
        return 5
    else:
        rows = [row for row in output1 if isinstance(row, dict)]

    raw_row_count = len(rows)

    if args.order_no:
        order_no = args.order_no.strip()
        rows = [
            row for row in rows
            if str(row.get("odno", "")).strip() == order_no
        ]

    all_keys: set[str] = set()
    for row in rows:
        all_keys.update(row.keys())

    interesting_keys = _interesting_keys(all_keys)

    _print_header("Summary")
    print(f"mode           : {settings.mode}")
    print(f"date range     : {args.start_date} .. {args.end_date}")
    print(f"rt_cd          : {response.rt_cd}")
    print(f"msg_cd         : {response.msg_cd}")
    print(f"msg            : {response.msg}")
    print(f"raw row count  : {raw_row_count}")
    print(f"filtered rows  : {len(rows)}")
    print(f"order filter   : {args.order_no!r}")

    _print_header("Interesting Keys")
    if interesting_keys:
        for key in interesting_keys:
            print(key)
    else:
        print("(no interesting keys found)")

    _print_header("Sample Rows")
    if not rows:
        print("(no rows)")
    else:
        sample_count = max(1, args.limit)
        for index, row in enumerate(rows[:sample_count], start=1):
            interesting = _interesting_fields(row)
            print(f"[row {index}]")
            if interesting:
                print(json.dumps(interesting, ensure_ascii=False, indent=2))
            else:
                print(json.dumps(row, ensure_ascii=False, indent=2))
            print()

    if args.output:
        payload = {
            "requested": {
                "start_date": args.start_date,
                "end_date": args.end_date,
                "order_no": args.order_no,
            },
            "rt_cd": response.rt_cd,
            "msg_cd": response.msg_cd,
            "msg": response.msg,
            "raw_row_count": raw_row_count,
            "filtered_row_count": len(rows),
            "all_keys": sorted(all_keys),
            "interesting_keys": interesting_keys,
            "rows": rows,
            "full_body": response.body,
        }
        saved_path = _save_json(args.output, payload)
        print(f"[OK] raw json saved: {saved_path}")

    if not rows:
        print(
            "[WARN] No filled rows returned. "
            "Try a wider date range that includes a known fill day."
        )
        return 3

    print(
        "[OK] Probe completed. "
        "Now check whether there is a true execution-id field and actual fill-price field."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
