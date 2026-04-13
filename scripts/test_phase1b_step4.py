"""
Phase 1-B Step 4 검증.

KisBroker Facade 통합 검증 (네트워크 없음, MockKisClient 사용).

검증 항목:
    1. KisBroker import / BrokerInterface 구현 확인
    2. __init__.py export 확인 (Phase 1-A + Phase 1-B 전체)
    3. place_order Facade 위임 정상
    4. cancel_order Facade 위임 정상
    5. get_order_status(미체결) Facade 위임 정상
    6. get_order_status(체결) Facade 위임 정상
    7. Phase 1-A 메서드 회귀 없음 (get_balance 호출 가능 확인)
    8. context manager 정상

실행:
    python scripts/test_phase1b_step4.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

KST = pytz.timezone("Asia/Seoul")


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


def _make_kis_response(body: dict, tr_id: str = "VTTC0802U") -> object:
    from broker.kis.models import KisResponse
    return KisResponse(
        body=body, rt_cd="0", msg_cd="APBK0013",
        msg="정상", tr_cont="", tr_id=tr_id, http_status=200,
    )


def _order_response(order_no: str = "0000117057") -> object:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "APBK0013", "msg1": "완료",
        "output": {"ODNO": order_no, "ORD_TMD": "141028",
                   "KRX_FWDG_ORD_ORGNO": "00060"},
    })


def _cancel_response(order_no: str = "0000117099") -> object:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "APBK0013", "msg1": "취소 완료",
        "output": {"ODNO": order_no, "ORD_TMD": "143000",
                   "KRX_FWDG_ORD_ORGNO": "00060"},
    })


def _pending_response() -> object:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상",
        "output": [{
            "odno": "0000117057", "pdno": "005930",
            "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
            "ord_qty": "10", "ord_unpr": "70000",
            "tot_ccld_qty": "0", "psbl_qty": "10",
            "ord_dt": "20240101", "ord_tmd": "141028",
        }],
    }, tr_id="VTTC8036R")


def _filled_response() -> object:
    return _make_kis_response({
        "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상",
        "output1": [{
            "odno": "0000117057", "pdno": "005930",
            "sll_buy_dvsn_cd": "02", "ord_dvsn_cd": "00",
            "ord_qty": "10", "ord_unpr": "70000",
            "tot_ccld_qty": "10", "cncl_yn": "N",
            "ord_dt": "20240101", "ord_tmd": "141028",
        }],
        "output2": [],
    }, tr_id="VTTC8001R")


def _make_broker_with_mock_order() -> tuple:
    """
    KisBroker 인스턴스 생성 후 내부 _order를 MagicMock으로 교체.
    네트워크 호출 없이 Facade 위임만 검증.
    """
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisBroker

    settings = load_settings()
    setup_logging(settings)
    broker = KisBroker(settings)
    mock_order = MagicMock()
    broker._order = mock_order
    return broker, mock_order


def main() -> int:
    print("=" * 60)
    print(" Phase 1-B Step 4 검증 (KisBroker Facade)")
    print("=" * 60)

    # --------------------------------------------------------
    # 1. import / BrokerInterface 구현
    # --------------------------------------------------------
    print("\n[1] import / BrokerInterface 구현")
    try:
        from broker.kis import KisBroker
        from broker.base import BrokerInterface
        from config.loader import load_settings
        from logger import setup_logging
    except ImportError as e:
        _fail("import", str(e))
        return 1
    _ok("import 성공")

    settings = load_settings()
    setup_logging(settings)

    broker = KisBroker(settings)
    if not isinstance(broker, BrokerInterface):
        _fail("isinstance(BrokerInterface)")
        return 1
    _ok("isinstance(BrokerInterface)")

    required_methods = [
        "get_access_token", "get_current_price",
        "get_daily_candles", "get_minute_candles",
        "get_balance", "place_order", "cancel_order",
        "get_order_status", "close",
    ]
    for m in required_methods:
        if not callable(getattr(broker, m, None)):
            _fail(f"메서드: {m}", "없음")
            return 1
    _ok(f"필수 메서드 {len(required_methods)}개 (Phase 1-A + 1-B)")
    broker.close()

    # --------------------------------------------------------
    # 2. __init__.py export 확인
    # --------------------------------------------------------
    print("\n[2] __init__.py export")
    import broker.kis as kis_pkg

    expected_exports = {
        # 컴포넌트
        "Account", "KisAuth", "KisBroker", "KisClient", "Order", "Quote",
        # 예외
        "KisError", "KisAuthError", "KisApiError",
        "KisOrderError", "KisParseError", "KisRateLimitError",
        # 모델 1-A
        "Balance", "Holding", "KisResponse", "PriceSnapshot",
        # 모델 1-B
        "OrderInfo", "OrderSide", "OrderStatus", "OrderType",
    }
    missing = expected_exports - set(dir(kis_pkg))
    if missing:
        _fail("누락 export", str(missing))
        return 1
    _ok(f"export {len(expected_exports)}개 전부 확인")

    # --------------------------------------------------------
    # 3. place_order Facade 위임
    # --------------------------------------------------------
    print("\n[3] place_order Facade 위임")
    from broker.kis.models import OrderStatus

    broker, mock_order = _make_broker_with_mock_order()
    from broker.kis.models import OrderInfo, OrderSide, OrderType
    from datetime import datetime

    expected_info = OrderInfo(
        code="005930", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        quantity=10, price=70000, status=OrderStatus.ACCEPTED,
        order_no="0000117057", filled_qty=0,
        timestamp=datetime.now(KST), raw_response={},
    )
    mock_order.place_order.return_value = expected_info

    result = broker.place_order("005930", "buy", 10, 70000)

    mock_order.place_order.assert_called_once_with("005930", "buy", 10, 70000)
    if result is not expected_info:
        _fail("반환값 동일성")
        return 1
    _ok("place_order → _order.place_order 위임 및 반환값 정상")
    broker.close()

    # --------------------------------------------------------
    # 4. cancel_order Facade 위임
    # --------------------------------------------------------
    print("\n[4] cancel_order Facade 위임")

    broker, mock_order = _make_broker_with_mock_order()
    expected_cancel = OrderInfo(
        code="005930", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        quantity=10, price=70000, status=OrderStatus.CANCELLED,
        order_no="0000117099", filled_qty=0,
        timestamp=datetime.now(KST), raw_response={},
    )
    mock_order.cancel_order.return_value = expected_cancel

    result = broker.cancel_order("0000117057", "005930", 10)

    mock_order.cancel_order.assert_called_once_with("0000117057", "005930", 10)
    if result is not expected_cancel:
        _fail("반환값 동일성")
        return 1
    _ok("cancel_order → _order.cancel_order 위임 및 반환값 정상")
    broker.close()

    # --------------------------------------------------------
    # 5. get_order_status(미체결) Facade 위임
    # --------------------------------------------------------
    print("\n[5] get_order_status(미체결) Facade 위임")

    broker, mock_order = _make_broker_with_mock_order()
    mock_order.get_order_status.return_value = []

    broker.get_order_status()
    mock_order.get_order_status.assert_called_once_with(None, filled_only=False)
    _ok("get_order_status() → filled_only=False 위임 정상")
    broker.close()

    # --------------------------------------------------------
    # 6. get_order_status(체결) Facade 위임
    # --------------------------------------------------------
    print("\n[6] get_order_status(체결) Facade 위임")

    broker, mock_order = _make_broker_with_mock_order()
    mock_order.get_order_status.return_value = []

    broker.get_order_status("0000117057", filled_only=True)
    mock_order.get_order_status.assert_called_once_with(
        "0000117057", filled_only=True
    )
    _ok("get_order_status(order_no, filled_only=True) 위임 정상")
    broker.close()

    # --------------------------------------------------------
    # 7. Phase 1-A 회귀: get_balance 호출 가능 확인
    # --------------------------------------------------------
    print("\n[7] Phase 1-A 회귀")

    broker2 = KisBroker(settings)
    mock_account = MagicMock()
    broker2._account = mock_account
    from broker.kis.models import Balance
    from datetime import datetime
    mock_account.get_balance.return_value = Balance(
        cash=30_000_000, available_cash=30_000_000,
        total_eval=30_000_000, total_profit=0,
    )

    bal = broker2.get_balance()
    mock_account.get_balance.assert_called_once()
    if bal.cash != 30_000_000:
        _fail("get_balance 회귀", str(bal.cash))
        return 1
    _ok("get_balance 회귀 없음")
    broker2.close()

    # --------------------------------------------------------
    # 8. context manager
    # --------------------------------------------------------
    print("\n[8] context manager")

    try:
        with KisBroker(settings) as b:
            if not isinstance(b, BrokerInterface):
                _fail("with 내부 타입")
                return 1
    except Exception as e:
        _fail("with", str(e))
        return 1
    _ok("with KisBroker(settings) as b 정상")

    # --------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step B-4 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step B-5 (KIS 실제 호출 통합 테스트)")
    return 0


if __name__ == "__main__":
    sys.exit(main())