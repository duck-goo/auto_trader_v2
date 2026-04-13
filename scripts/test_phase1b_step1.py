"""
Phase 1-B Step 1 검증.

검증 항목 (네트워크 없음):
    1. OrderSide / OrderType / OrderStatus enum
    2. OrderInfo dataclass (frozen, 필드 타입)
    3. KisOrderError
    4. endpoints 상수 5개
    5. BrokerInterface abstract 메서드 3개
    6. Phase 1-A 기존 모델 회귀 없음

실행:
    python scripts/test_phase1b_step1.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


def main() -> int:
    print("=" * 60)
    print(" Phase 1-B Step 1 검증")
    print("=" * 60)

    # --------------------------------------------------------
    # 1. 신규 enum import
    # --------------------------------------------------------
    print("\n[1] 신규 enum")
    try:
        from broker.kis.models import OrderSide, OrderType, OrderStatus
    except ImportError as e:
        _fail("enum import", str(e))
        return 1

    # OrderSide
    if OrderSide.BUY != "buy":
        _fail("OrderSide.BUY == 'buy'")
        return 1
    if OrderSide.SELL != "sell":
        _fail("OrderSide.SELL == 'sell'")
        return 1
    _ok("OrderSide (BUY/SELL, str 비교)")

    # OrderType
    if OrderType.MARKET != "market" or OrderType.LIMIT != "limit":
        _fail("OrderType 값")
        return 1
    _ok("OrderType (MARKET/LIMIT)")

    # OrderStatus 7가지 모두 존재 확인
    expected_statuses = {
        "pending", "accepted", "filled",
        "partial", "cancelled", "rejected", "unknown",
    }
    actual_statuses = {s.value for s in OrderStatus}
    if actual_statuses != expected_statuses:
        _fail("OrderStatus", f"기대={expected_statuses}, 실제={actual_statuses}")
        return 1
    _ok("OrderStatus (7가지)")

    # --------------------------------------------------------
    # 2. OrderInfo dataclass
    # --------------------------------------------------------
    print("\n[2] OrderInfo dataclass")
    try:
        from broker.kis.models import OrderInfo
        from datetime import datetime
        import pytz
    except ImportError as e:
        _fail("OrderInfo import", str(e))
        return 1

    KST = pytz.timezone("Asia/Seoul")
    now = datetime.now(KST)

    info = OrderInfo(
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=70000,
        status=OrderStatus.ACCEPTED,
        order_no="0000123456",
        filled_qty=0,
        timestamp=now,
        raw_response={"odno": "0000123456"},
    )

    # frozen 확인
    try:
        info.status = OrderStatus.FILLED  # type: ignore[misc]
        _fail("OrderInfo frozen", "변경이 허용됨")
        return 1
    except Exception:
        _ok("OrderInfo frozen")

    # 필드 값 확인
    assert info.code == "005930"
    assert info.side == OrderSide.BUY
    assert info.order_no == "0000123456"
    assert info.filled_qty == 0
    _ok("OrderInfo 필드 정상")

    # order_no=None 케이스 (PENDING/UNKNOWN)
    info_pending = OrderInfo(
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        price=0,
        status=OrderStatus.PENDING,
        order_no=None,
        filled_qty=0,
        timestamp=now,
        raw_response={},
    )
    assert info_pending.order_no is None
    _ok("OrderInfo order_no=None (PENDING 케이스)")

    # --------------------------------------------------------
    # 3. KisOrderError
    # --------------------------------------------------------
    print("\n[3] KisOrderError")
    try:
        from broker.kis.errors import KisOrderError, KisError
    except ImportError as e:
        _fail("KisOrderError import", str(e))
        return 1

    if not issubclass(KisOrderError, KisError):
        _fail("KisOrderError ⊂ KisError")
        return 1

    err = KisOrderError("중복 주문", order_info=info)
    assert err.order_info is info
    _ok("KisOrderError (상속, order_info 보존)")

    # order_info 없는 케이스
    err2 = KisOrderError("취소 실패")
    assert err2.order_info is None
    _ok("KisOrderError order_info=None 케이스")

    # --------------------------------------------------------
    # 4. endpoints 상수
    # --------------------------------------------------------
    print("\n[4] 주문 endpoints 상수")
    try:
        from broker.kis.endpoints import (
            PATH_ORDER_CASH,
            TR_ID_BUY,
            TR_ID_SELL,
            PATH_ORDER_RVSECNCL,
            TR_ID_CANCEL,
            PATH_INQUIRE_PSBL_RVSECNCL,
            TR_ID_INQUIRE_PSBL_RVSECNCL,
            PATH_INQUIRE_DAILY_CCLD,
            TR_ID_INQUIRE_DAILY_CCLD,
        )
    except ImportError as e:
        _fail("endpoints import", str(e))
        return 1

    # TR_ID 형식 검증 (T로 시작해야 모의 자동변환 대상)
    for name, tr_id in [
        ("TR_ID_BUY", TR_ID_BUY),
        ("TR_ID_SELL", TR_ID_SELL),
        ("TR_ID_CANCEL", TR_ID_CANCEL),
        ("TR_ID_INQUIRE_PSBL_RVSECNCL", TR_ID_INQUIRE_PSBL_RVSECNCL),
        ("TR_ID_INQUIRE_DAILY_CCLD", TR_ID_INQUIRE_DAILY_CCLD),
    ]:
        if not tr_id.startswith("T"):
            _fail(f"{name}", f"T로 시작해야 함: {tr_id!r}")
            return 1
    _ok("TR_ID 5개 모두 T로 시작 (모의 자동변환 대상)")

    # PATH 형식 검증
    for name, path in [
        ("PATH_ORDER_CASH", PATH_ORDER_CASH),
        ("PATH_ORDER_RVSECNCL", PATH_ORDER_RVSECNCL),
        ("PATH_INQUIRE_PSBL_RVSECNCL", PATH_INQUIRE_PSBL_RVSECNCL),
        ("PATH_INQUIRE_DAILY_CCLD", PATH_INQUIRE_DAILY_CCLD),
    ]:
        if not path.startswith("/uapi/"):
            _fail(f"{name}", f"'/uapi/'로 시작해야 함: {path!r}")
            return 1
    _ok("PATH 4개 형식 정상")

    # --------------------------------------------------------
    # 5. BrokerInterface abstract 메서드
    # --------------------------------------------------------
    print("\n[5] BrokerInterface abstract 메서드")
    try:
        from broker.base import BrokerInterface
    except ImportError as e:
        _fail("BrokerInterface import", str(e))
        return 1

    # abstract 클래스 직접 인스턴스화 시도 → TypeError 기대
    try:
        BrokerInterface()  # type: ignore[abstract]
        _fail("abstract 직접 인스턴스화", "TypeError 없음")
        return 1
    except TypeError:
        _ok("BrokerInterface 직접 인스턴스화 불가 (abstract)")

    # 신규 abstract 메서드 3개 존재 확인
    new_methods = ["place_order", "cancel_order", "get_order_status"]
    for m in new_methods:
        if not hasattr(BrokerInterface, m):
            _fail(f"abstract 메서드: {m}", "없음")
            return 1
    _ok(f"신규 abstract 메서드 {len(new_methods)}개 존재")

    # 구현 누락 시 TypeError 발생 확인 (place_order만 빠진 케이스)
    import pandas as pd
    from broker.kis.models import Balance, PriceSnapshot

    class _PartialBroker(BrokerInterface):
        """place_order 미구현 - TypeError 발생해야 함."""
        def get_access_token(self) -> str: return ""
        def get_current_price(self, code): ...
        def get_daily_candles(self, code, count=30, end_date=None): ...
        def get_minute_candles(self, code, interval="1"): ...
        def get_balance(self): ...
        def cancel_order(self, order_no, code, quantity): ...
        def get_order_status(self, order_no=None, *, filled_only=False): ...
        # place_order 누락!

    try:
        _PartialBroker()
        _fail("place_order 미구현", "TypeError 없음")
        return 1
    except TypeError:
        _ok("place_order 미구현 시 TypeError 정상")

    # --------------------------------------------------------
    # 6. Phase 1-A 회귀 검증
    # --------------------------------------------------------
    print("\n[6] Phase 1-A 회귀")
    try:
        from broker.kis.models import (
            PriceSnapshot, Holding, Balance, KisResponse,
        )
        from broker.kis.errors import (
            KisError, KisAuthError, KisApiError,
            KisParseError, KisRateLimitError,
        )
        from broker.kis.endpoints import (
            PATH_INQUIRE_PRICE, TR_ID_INQUIRE_PRICE,
            PATH_INQUIRE_BALANCE, TR_ID_INQUIRE_BALANCE,
        )
    except ImportError as e:
        _fail("Phase 1-A import 회귀", str(e))
        return 1

    snap = PriceSnapshot(
        code="005930", name="삼성전자",
        price=70000, open=69500, high=70500, low=69000,
        prev_close=69800, change=200, change_rate=0.29,
        volume=1234567, timestamp=now,
    )
    assert snap.price == 70000
    _ok("PriceSnapshot 회귀 없음")

    bal = Balance(
        cash=1_000_000, available_cash=950_000,
        total_eval=1_000_000, total_profit=0,
    )
    assert bal.holding_count == 0
    _ok("Balance 회귀 없음")

    # --------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step B-1 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step B-2 (parsers.py 주문 파서)")
    return 0


if __name__ == "__main__":
    sys.exit(main())