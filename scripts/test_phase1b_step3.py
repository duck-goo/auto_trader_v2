"""
Phase 1-B Step 3 검증.

MockKisClient로 네트워크 없이 Order 클래스 전체 동작 검증.

검증 항목:
    1. 초기화 및 계좌번호 분리
    2. place_order - ACCEPTED 정상
    3. place_order - 중복 주문 차단
    4. place_order - KisApiError(REJECTED) 전파
    5. place_order - 네트워크 실패 → UNKNOWN KisOrderError
    6. place_order - 중복 키 finally 해제 확인
    7. cancel_order - CANCELLED 정상
    8. cancel_order - 입력값 검증
    9. get_order_status - 미체결 조회
    10. get_order_status - 체결 조회
    11. get_order_status - order_no 필터
    12. _validate_order_inputs 경계값

실행:
    python scripts/test_phase1b_step3.py
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

KST = pytz.timezone("Asia/Seoul")


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


# ============================================================
# MockKisClient 헬퍼
# ============================================================

def _make_kis_response(body: dict, tr_id: str = "VTTC0802U") -> Any:
    from broker.kis.models import KisResponse
    return KisResponse(
        body=body, rt_cd="0", msg_cd="APBK0013",
        msg="정상", tr_cont="", tr_id=tr_id, http_status=200,
    )


def _order_accepted_response(order_no: str = "0000117057") -> Any:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "APBK0013", "msg1": "주문 전송 완료",
        "output": {"ODNO": order_no, "ORD_TMD": "141028",
                   "KRX_FWDG_ORD_ORGNO": "00060"},
    })


def _cancel_accepted_response(order_no: str = "0000117099") -> Any:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "APBK0013", "msg1": "취소 완료",
        "output": {"ODNO": order_no, "ORD_TMD": "143000",
                   "KRX_FWDG_ORD_ORGNO": "00060"},
    })


def _pending_list_response(items: list[dict]) -> Any:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상",
        "output": items,
    }, tr_id="VTTC8036R")


def _filled_list_response(items: list[dict]) -> Any:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상",
        "output1": items, "output2": [],
    }, tr_id="VTTC8001R")


def _make_order(settings: Any) -> Any:
    """MockKisClient를 주입한 Order 인스턴스 반환."""
    from broker.kis.order import Order
    mock_client = MagicMock()
    return Order(mock_client, settings), mock_client


def _make_settings(account_no: str = "50123456-01") -> Any:
    """최소한의 settings Mock."""
    s = MagicMock()
    s.kis_account_no = account_no
    s.mode = "mock"
    return s


# ============================================================
# 테스트
# ============================================================

def main() -> int:
    print("=" * 60)
    print(" Phase 1-B Step 3 검증 (Order 클래스)")
    print("=" * 60)

    try:
        from broker.kis.order import Order, _split_account_no, _validate_order_inputs
        from broker.kis.models import OrderSide, OrderStatus, OrderType
        from broker.kis.errors import KisApiError, KisOrderError
    except ImportError as e:
        _fail("import", str(e))
        return 1
    _ok("import 성공")

    # --------------------------------------------------------
    # 1. 초기화 / 계좌번호 분리
    # --------------------------------------------------------
    print("\n[1] 초기화 / 계좌번호 분리")

    cano, acnt = _split_account_no("50123456-01")
    if cano != "50123456" or acnt != "01":
        _fail("_split_account_no", f"cano={cano}, acnt={acnt}")
        return 1
    _ok("_split_account_no 정상")

    try:
        _split_account_no("1234-01")
        _fail("짧은 계좌번호", "예외 없음")
        return 1
    except ValueError:
        _ok("짧은 계좌번호 → ValueError")

    order, _ = _make_order(_make_settings())
    if order._cano != "50123456" or order._acnt_prdt_cd != "01":
        _fail("Order 내부 계좌번호", f"{order._cano}-{order._acnt_prdt_cd}")
        return 1
    _ok("Order 초기화 계좌번호 분리 정상")

    # --------------------------------------------------------
    # 2. place_order - ACCEPTED 정상
    # --------------------------------------------------------
    print("\n[2] place_order - ACCEPTED")

    order, mock_client = _make_order(_make_settings())
    mock_client.request_post.return_value = _order_accepted_response("0000117057")

    info = order.place_order("005930", "buy", 10, 70000)
    if info.status != OrderStatus.ACCEPTED:
        _fail("status", str(info.status))
        return 1
    if info.order_no != "0000117057":
        _fail("order_no", str(info.order_no))
        return 1
    if info.side != OrderSide.BUY:
        _fail("side", str(info.side))
        return 1
    if info.order_type != OrderType.LIMIT:
        _fail("order_type", str(info.order_type))
        return 1
    if info.quantity != 10 or info.price != 70000:
        _fail("qty/price", f"{info.quantity}/{info.price}")
        return 1
    _ok("ACCEPTED / order_no / side / type / qty / price 정상")

    # 시장가 케이스
    order2, mock_client2 = _make_order(_make_settings())
    mock_client2.request_post.return_value = _order_accepted_response("0000117058")
    info_m = order2.place_order("005930", "sell", 5, 0)
    if info_m.order_type != OrderType.MARKET or info_m.price != 0:
        _fail("시장가", f"type={info_m.order_type}, price={info_m.price}")
        return 1
    _ok("시장가(price=0) → MARKET 정상")

    # --------------------------------------------------------
    # 3. place_order - 중복 주문 차단
    # --------------------------------------------------------
    print("\n[3] place_order - 중복 주문 차단")

    order3, mock_client3 = _make_order(_make_settings())
    # _pending_set에 직접 키 삽입 (진행 중 상태 시뮬레이션)
    order3._pending_set.add("005930:buy")

    try:
        order3.place_order("005930", "buy", 10, 70000)
        _fail("중복 차단", "예외 없음")
        return 1
    except KisOrderError as e:
        if "중복" not in str(e):
            _fail("중복 에러 메시지", str(e))
            return 1
        _ok("중복 주문 → KisOrderError('중복')")

    # 다른 side는 통과 확인
    mock_client3.request_post.return_value = _order_accepted_response()
    info_sell = order3.place_order("005930", "sell", 10, 70000)
    if info_sell.status != OrderStatus.ACCEPTED:
        _fail("다른 side 통과", str(info_sell.status))
        return 1
    _ok("sell은 buy 진행 중에도 통과 (다른 키)")

    # --------------------------------------------------------
    # 4. place_order - KisApiError(REJECTED) 전파
    # --------------------------------------------------------
    print("\n[4] place_order - KisApiError 전파")

    order4, mock_client4 = _make_order(_make_settings())
    mock_client4.request_post.side_effect = KisApiError(
        "잔량 부족", rt_cd="1", msg_cd="APBK0014",
        msg="주문수량이 잔고를 초과합니다."
    )

    try:
        order4.place_order("005930", "buy", 9999, 70000)
        _fail("KisApiError 전파", "예외 없음")
        return 1
    except KisApiError:
        _ok("KisApiError 전파 정상")

    # 키가 finally에서 제거되었는지 확인
    if "005930:buy" in order4._pending_set:
        _fail("KisApiError 후 키 잔존", str(order4._pending_set))
        return 1
    _ok("KisApiError 후 _pending_set 키 제거 확인")

    # --------------------------------------------------------
    # 5. place_order - 네트워크 실패 → UNKNOWN
    # --------------------------------------------------------
    print("\n[5] place_order - 네트워크 실패 → UNKNOWN")

    order5, mock_client5 = _make_order(_make_settings())
    mock_client5.request_post.side_effect = Exception("Connection reset")

    try:
        order5.place_order("005930", "buy", 10, 70000)
        _fail("UNKNOWN", "예외 없음")
        return 1
    except KisOrderError as e:
        if e.order_info is None:
            _fail("UNKNOWN order_info", "None")
            return 1
        if e.order_info.status != OrderStatus.UNKNOWN:
            _fail("UNKNOWN status", str(e.order_info.status))
            return 1
        if "UNKNOWN" not in str(e):
            _fail("UNKNOWN 메시지", str(e))
            return 1
        _ok("네트워크 실패 → KisOrderError(UNKNOWN, order_info 포함)")

    # --------------------------------------------------------
    # 6. finally 키 해제 - 실패 후 재주문 가능 확인
    # --------------------------------------------------------
    print("\n[6] finally 키 해제 후 재주문 가능")

    order6, mock_client6 = _make_order(_make_settings())
    mock_client6.request_post.side_effect = [
        Exception("1차 실패"),                          # 1차: 실패
        _order_accepted_response("0000117060"),         # 2차: 성공
    ]

    try:
        order6.place_order("005930", "buy", 10, 70000)
    except KisOrderError:
        pass  # 1차 실패 기대

    # _pending_set이 비어있어야 2차 주문 가능
    if "005930:buy" in order6._pending_set:
        _fail("1차 실패 후 키 잔존", str(order6._pending_set))
        return 1

    info_retry = order6.place_order("005930", "buy", 10, 70000)
    if info_retry.status != OrderStatus.ACCEPTED:
        _fail("2차 주문 실패", str(info_retry.status))
        return 1
    _ok("실패 후 _pending_set 해제 → 재주문 ACCEPTED 정상")

    # --------------------------------------------------------
    # 7. cancel_order - CANCELLED 정상
    # --------------------------------------------------------
    print("\n[7] cancel_order - CANCELLED")

    order7, mock_client7 = _make_order(_make_settings())
    mock_client7.request_post.return_value = _cancel_accepted_response("0000117099")

    cancel_info = order7.cancel_order("0000117057", "005930", 10)
    if cancel_info.status != OrderStatus.CANCELLED:
        _fail("status", str(cancel_info.status))
        return 1
    _ok("CANCELLED 정상")

    # body에 ORGN_ODNO 포함 확인
    call_args = mock_client7.request_post.call_args
    body_sent = call_args.kwargs.get("body") or call_args[1].get("body", {})
    if body_sent.get("ORGN_ODNO") != "0000117057":
        _fail("ORGN_ODNO", str(body_sent.get("ORGN_ODNO")))
        return 1
    if body_sent.get("QTY_ALL_ORD_YN") != "Y":
        _fail("QTY_ALL_ORD_YN", str(body_sent.get("QTY_ALL_ORD_YN")))
        return 1
    _ok("cancel body: ORGN_ODNO / QTY_ALL_ORD_YN=Y 확인")

    # --------------------------------------------------------
    # 8. cancel_order - 입력값 검증
    # --------------------------------------------------------
    print("\n[8] cancel_order - 입력값 검증")

    order8, _ = _make_order(_make_settings())
    cases = [
        ("", "005930", 10, "order_no 빈값"),
        ("0000117057", "00593",  10, "종목코드 5자리"),
        ("0000117057", "005930", 0,  "quantity=0"),
        ("0000117057", "005930", -1, "quantity 음수"),
    ]
    for order_no, code, qty, desc in cases:
        try:
            order8.cancel_order(order_no, code, qty)
            _fail(desc, "예외 없음")
            return 1
        except ValueError:
            pass
    _ok("cancel_order 입력값 검증 4케이스")

    # --------------------------------------------------------
    # 9. get_order_status - 미체결 조회
    # --------------------------------------------------------
    print("\n[9] get_order_status - 미체결")

    order9, mock_client9 = _make_order(_make_settings())
    mock_client9.request_get.return_value = _pending_list_response([
        {
            "odno": "0000117057", "pdno": "005930",
            "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
            "ord_qty": "10", "ord_unpr": "70000",
            "tot_ccld_qty": "0", "psbl_qty": "10",
            "ord_dt": "20240101", "ord_tmd": "141028",
        }
    ])

    orders = order9.get_order_status()
    if len(orders) != 1:
        _fail("미체결 건수", str(len(orders)))
        return 1
    if orders[0].order_no != "0000117057":
        _fail("order_no", str(orders[0].order_no))
        return 1
    _ok("미체결 조회 1건 정상")

    # --------------------------------------------------------
    # 10. get_order_status - 체결 조회
    # --------------------------------------------------------
    print("\n[10] get_order_status - 체결")

    order10, mock_client10 = _make_order(_make_settings())
    mock_client10.request_get.return_value = _filled_list_response([
        {
            "odno": "0000117057", "pdno": "005930",
            "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
            "ord_qty": "10", "ord_unpr": "70000",
            "tot_ccld_qty": "10", "cncl_yn": "N",
            "ord_dt": "20240101", "ord_tmd": "141028",
        }
    ])

    filled = order10.get_order_status(filled_only=True)
    if len(filled) != 1:
        _fail("체결 건수", str(len(filled)))
        return 1
    if filled[0].status != OrderStatus.FILLED:
        _fail("FILLED", str(filled[0].status))
        return 1
    _ok("체결 조회 1건(FILLED) 정상")

    # --------------------------------------------------------
    # 11. get_order_status - order_no 필터
    # --------------------------------------------------------
    print("\n[11] get_order_status - order_no 필터")

    order11, mock_client11 = _make_order(_make_settings())
    mock_client11.request_get.return_value = _pending_list_response([
        {
            "odno": "0000000001", "pdno": "005930",
            "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
            "ord_qty": "5", "ord_unpr": "70000",
            "tot_ccld_qty": "0", "psbl_qty": "5",
            "ord_dt": "20240101", "ord_tmd": "141028",
        },
        {
            "odno": "0000000002", "pdno": "000660",
            "sll_buy_dvsn_cd": "01", "ord_dvsn_cd": "00",
            "ord_qty": "3", "ord_unpr": "120000",
            "tot_ccld_qty": "0", "psbl_qty": "3",
            "ord_dt": "20240101", "ord_tmd": "141100",
        },
    ])

    filtered = order11.get_order_status(order_no="0000000001")
    if len(filtered) != 1:
        _fail("필터 결과 건수", str(len(filtered)))
        return 1
    if filtered[0].order_no != "0000000001":
        _fail("필터 order_no", str(filtered[0].order_no))
        return 1
    _ok("order_no 필터 정상 (2건 중 1건)")

    not_found = order11.get_order_status(order_no="9999999999")
    if not_found != []:
        _fail("없는 order_no 필터", str(not_found))
        return 1
    _ok("없는 order_no → 빈 리스트")

    # --------------------------------------------------------
    # 12. _validate_order_inputs 경계값
    # --------------------------------------------------------
    print("\n[12] _validate_order_inputs 경계값")

    from broker.kis.order import _validate_order_inputs

    bad_cases = [
        ("0059300", "buy", 10, 0, "종목코드 7자리"),
        ("005930", "BUY",  10, 0, "대문자 side"),
        ("005930", "buy",  0,  0, "quantity=0"),
        ("005930", "buy", -1,  0, "quantity 음수"),
        ("005930", "buy",  1, -1, "price 음수"),
    ]
    for code, side, qty, price, desc in bad_cases:
        try:
            _validate_order_inputs(code, side, qty, price)
            _fail(desc, "예외 없음")
            return 1
        except ValueError:
            pass
    _ok("ValueError 5케이스")

    # 정상 케이스
    s, t = _validate_order_inputs("005930", "buy", 1, 0)
    if s != OrderSide.BUY or t != OrderType.MARKET:
        _fail("시장가 정상", f"{s},{t}")
        return 1
    s2, t2 = _validate_order_inputs("005930", "sell", 10, 70000)
    if s2 != OrderSide.SELL or t2 != OrderType.LIMIT:
        _fail("지정가 정상", f"{s2},{t2}")
        return 1
    _ok("정상 케이스 (시장가/지정가)")

    # --------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step B-3 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step B-4 (broker.py 연결 + __init__.py 갱신)")
    return 0


if __name__ == "__main__":
    sys.exit(main())