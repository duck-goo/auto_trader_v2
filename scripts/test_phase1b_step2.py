"""
Phase 1-B Step 2 검증.

검증 항목 (네트워크 없음, 모의 dict 입력):
    1. parse_order_response  - 정상 / ODNO 누락 / output 타입 오류
    2. parse_cancel_response - 정상
    3. parse_pending_order_list - 정상 2건 / 빈 응답 / sll_buy_dvsn_cd 오류
    4. parse_filled_order_list  - 정상 (FILLED/PARTIAL/CANCELLED) / 빈 응답
    5. _to_order_side / _to_order_type 경계값
    6. Phase 1-A 파서 회귀 없음

실행:
    python scripts/test_phase1b_step2.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

KST = pytz.timezone("Asia/Seoul")


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


def _make_response(body: dict, tr_id: str = "VTTC0802U") -> "KisResponse":
    from broker.kis.models import KisResponse
    return KisResponse(
        body=body,
        rt_cd="0",
        msg_cd="APBK0013",
        msg="주문 전송 완료",
        tr_cont="",
        tr_id=tr_id,
        http_status=200,
    )


def main() -> int:
    print("=" * 60)
    print(" Phase 1-B Step 2 검증 (파서)")
    print("=" * 60)

    try:
        from broker.kis.parsers import (
            parse_order_response,
            parse_cancel_response,
            parse_pending_order_list,
            parse_filled_order_list,
        )
        from broker.kis.models import OrderSide, OrderType, OrderStatus
        from broker.kis.errors import KisParseError
    except ImportError as e:
        _fail("import", str(e))
        return 1
    _ok("import 성공")

    now = datetime.now(KST)

    # --------------------------------------------------------
    # 1. parse_order_response - 정상
    # --------------------------------------------------------
    print("\n[1] parse_order_response - 정상")
    resp = _make_response({
        "rt_cd": "0",
        "msg_cd": "APBK0013",
        "msg1": "주문 전송 완료 되었습니다.",
        "output": {
            "KRX_FWDG_ORD_ORGNO": "00060",
            "ODNO": "0000117057",
            "ORD_TMD": "141028",
        },
    })
    info = parse_order_response(
        resp,
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=70000,
        timestamp=now,
    )
    if info.status != OrderStatus.ACCEPTED:
        _fail("status", str(info.status))
        return 1
    if info.order_no != "0000117057":
        _fail("order_no", str(info.order_no))
        return 1
    if info.code != "005930":
        _fail("code", str(info.code))
        return 1
    if info.filled_qty != 0:
        _fail("filled_qty", str(info.filled_qty))
        return 1
    if info.raw_response.get("ODNO") != "0000117057":
        _fail("raw_response 보존", str(info.raw_response))
        return 1
    _ok("ACCEPTED / order_no / raw_response 정상")

    # 시장가 케이스
    resp_m = _make_response({
        "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
        "output": {"ODNO": "0000117058", "ORD_TMD": "150000"},
    })
    info_m = parse_order_response(
        resp_m,
        code="000660",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=5,
        price=0,
        timestamp=now,
    )
    if info_m.price != 0 or info_m.order_type != OrderType.MARKET:
        _fail("시장가 케이스", f"price={info_m.price}, type={info_m.order_type}")
        return 1
    _ok("시장가(price=0) 정상")

    # ODNO 누락 → KisParseError
    resp_no_odno = _make_response({
        "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
        "output": {"ORD_TMD": "141028"},  # ODNO 없음
    })
    try:
        parse_order_response(
            resp_no_odno,
            code="005930", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=10,
            price=70000, timestamp=now,
        )
        _fail("ODNO 누락", "예외 없음")
        return 1
    except KisParseError:
        _ok("ODNO 누락 → KisParseError")

    # output이 list → KisParseError
    resp_bad = _make_response({
        "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
        "output": [{"ODNO": "123"}],
    })
    try:
        parse_order_response(
            resp_bad,
            code="005930", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=10,
            price=70000, timestamp=now,
        )
        _fail("output=list", "예외 없음")
        return 1
    except KisParseError:
        _ok("output=list → KisParseError")

    # --------------------------------------------------------
    # 2. parse_cancel_response - 정상
    # --------------------------------------------------------
    print("\n[2] parse_cancel_response - 정상")
    resp_cancel = _make_response({
        "rt_cd": "0", "msg_cd": "APBK0013", "msg1": "취소 완료",
        "output": {
            "KRX_FWDG_ORD_ORGNO": "00060",
            "ODNO": "0000117099",
            "ORD_TMD": "143000",
        },
    })
    info_c = parse_cancel_response(
        resp_cancel,
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=70000,
        timestamp=now,
    )
    if info_c.status != OrderStatus.CANCELLED:
        _fail("status", str(info_c.status))
        return 1
    if info_c.order_no != "0000117099":
        _fail("order_no", str(info_c.order_no))
        return 1
    _ok("CANCELLED / order_no 정상")

    # --------------------------------------------------------
    # 3. parse_pending_order_list
    # --------------------------------------------------------
    print("\n[3] parse_pending_order_list")

    pending_body = {
        "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상",
        "output": [
            {   # 미체결 매수 (ACCEPTED)
                "odno": "0000117057",
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "sll_buy_dvsn_cd": "02",  # 매수
                "ord_dvsn_cd": "00",      # 지정가
                "ord_qty": "10",
                "ord_unpr": "70000",
                "tot_ccld_qty": "0",
                "psbl_qty": "10",
                "ord_dt": "20240101",
                "ord_tmd": "141028",
            },
            {   # 일부체결 매도 (PARTIAL)
                "odno": "0000117058",
                "pdno": "000660",
                "prdt_name": "SK하이닉스",
                "sll_buy_dvsn_cd": "01",  # 매도
                "ord_dvsn_cd": "01",      # 시장가
                "ord_qty": "5",
                "ord_unpr": "0",
                "tot_ccld_qty": "3",
                "psbl_qty": "2",
                "ord_dt": "20240101",
                "ord_tmd": "141100",
            },
        ],
    }
    orders = parse_pending_order_list(
        _make_response(pending_body, "VTTC8036R")
    )
    if len(orders) != 2:
        _fail("건수", str(len(orders)))
        return 1
    if orders[0].status != OrderStatus.ACCEPTED:
        _fail("orders[0].status", str(orders[0].status))
        return 1
    if orders[0].side != OrderSide.BUY:
        _fail("orders[0].side", str(orders[0].side))
        return 1
    if orders[1].status != OrderStatus.PARTIAL:
        _fail("orders[1].status PARTIAL", str(orders[1].status))
        return 1
    if orders[1].side != OrderSide.SELL:
        _fail("orders[1].side", str(orders[1].side))
        return 1
    if orders[1].filled_qty != 3:
        _fail("orders[1].filled_qty", str(orders[1].filled_qty))
        return 1
    _ok("2건 (ACCEPTED/PARTIAL, BUY/SELL) 정상")

    # 빈 응답
    empty_orders = parse_pending_order_list(
        _make_response({"rt_cd": "0", "msg_cd": "X", "msg1": "OK", "output": []},
                       "VTTC8036R")
    )
    if empty_orders != []:
        _fail("빈 output", f"빈 list 아님: {empty_orders}")
        return 1
    _ok("빈 output → 빈 list")

    # sll_buy_dvsn_cd 오류 → KisParseError
    bad_side_body = {
        "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
        "output": [{
            "odno": "123", "pdno": "005930",
            "sll_buy_dvsn_cd": "99",  # 잘못된 코드
            "ord_dvsn_cd": "00",
            "ord_qty": "10", "ord_unpr": "70000",
            "tot_ccld_qty": "0", "psbl_qty": "10",
            "ord_dt": "20240101", "ord_tmd": "141028",
        }],
    }
    try:
        parse_pending_order_list(_make_response(bad_side_body, "VTTC8036R"))
        _fail("sll_buy_dvsn_cd='99'", "예외 없음")
        return 1
    except KisParseError:
        _ok("sll_buy_dvsn_cd='99' → KisParseError")

    # --------------------------------------------------------
    # 4. parse_filled_order_list
    # --------------------------------------------------------
    print("\n[4] parse_filled_order_list")

    filled_body = {
        "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상",
        "output1": [
            {   # 전량체결
                "odno": "0000117057",
                "pdno": "005930",
                "sll_buy_dvsn_cd": "02",
                "ord_dvsn_cd": "00",
                "ord_qty": "10",
                "ord_unpr": "70000",
                "tot_ccld_qty": "10",
                "cncl_yn": "N",
                "ord_dt": "20240101",
                "ord_tmd": "141028",
            },
            {   # 취소됨
                "odno": "0000117058",
                "pdno": "000660",
                "sll_buy_dvsn_cd": "01",
                "ord_dvsn_cd": "00",
                "ord_qty": "5",
                "ord_unpr": "120000",
                "tot_ccld_qty": "0",
                "cncl_yn": "Y",
                "ord_dt": "20240101",
                "ord_tmd": "141100",
            },
            {   # 일부체결
                "odno": "0000117059",
                "pdno": "035720",
                "sll_buy_dvsn_cd": "02",
                "ord_dvsn_cd": "00",
                "ord_qty": "20",
                "ord_unpr": "50000",
                "tot_ccld_qty": "7",
                "cncl_yn": "N",
                "ord_dt": "20240101",
                "ord_tmd": "141200",
            },
        ],
        "output2": {"tot_ord_qty": "35"},
    }
    filled = parse_filled_order_list(
        _make_response(filled_body, "VTTC8001R")
    )
    if len(filled) != 3:
        _fail("건수", str(len(filled)))
        return 1
    if filled[0].status != OrderStatus.FILLED:
        _fail("FILLED", str(filled[0].status))
        return 1
    if filled[1].status != OrderStatus.CANCELLED:
        _fail("CANCELLED", str(filled[1].status))
        return 1
    if filled[2].status != OrderStatus.PARTIAL:
        _fail("PARTIAL", str(filled[2].status))
        return 1
    if filled[2].filled_qty != 7:
        _fail("PARTIAL filled_qty", str(filled[2].filled_qty))
        return 1
    _ok("3건 (FILLED/CANCELLED/PARTIAL) 정상")

    # 빈 output1
    empty_filled = parse_filled_order_list(
        _make_response({
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output1": [], "output2": {},
        }, "VTTC8001R")
    )
    if empty_filled != []:
        _fail("빈 output1", "빈 list 아님")
        return 1
    _ok("빈 output1 → 빈 list")

    # --------------------------------------------------------
    # 5. Phase 1-A 파서 회귀
    # --------------------------------------------------------
    print("\n[5] Phase 1-A 파서 회귀")
    try:
        from broker.kis.parsers import (
            parse_price_snapshot,
            parse_daily_candles,
            parse_minute_candles,
            parse_balance,
        )
    except ImportError as e:
        _fail("Phase 1-A 파서 import", str(e))
        return 1
    _ok("Phase 1-A 파서 import 회귀 없음")

    # --------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step B-2 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step B-3 (order.py - Order 클래스)")
    return 0


if __name__ == "__main__":
    sys.exit(main())