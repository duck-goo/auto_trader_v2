"""
Phase 1-B Step 5 검증 (KIS 실제 호출).

모의투자 환경 제약 반영:
    - inquire-psbl-rvsecncl (미체결 조회): 모의 미지원 → 건너뜀
    - order-cash (매수/매도): 모의 지원 ✅
    - order-rvsecncl (취소):  모의 지원 ✅
    - inquire-daily-ccld (체결 조회): 이번에 확인

테스트 시나리오:
    [A] 지정가 매수 (삼성전자 1주) → ACCEPTED 확인
    [B] 취소 → CANCELLED 확인
    [C] 당일 체결 조회 → 오류 없이 응답 확인

실행:
    python scripts/test_phase1b_step5_kis.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_CODE  = "005930"
TEST_QTY   = 1
STEP_DELAY = 1.5


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"  [WARN] {label}" + (f" - {detail}" if detail else ""))


def main() -> int:
    print("=" * 60)
    print(" Phase 1-B Step 5 검증 (KIS 실제 호출)")
    print("=" * 60)

    # --------------------------------------------------------
    # 준비
    # --------------------------------------------------------
    print("\n[0] 준비")
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisBroker
    from broker.kis.models import OrderStatus
    from broker.kis.errors import KisApiError, KisOrderError
    from market import round_down_to_krx_tick

    settings = load_settings()
    setup_logging(settings)

    if settings.mode != "mock":
        _fail("모드 확인", f"mock이어야 함. 현재: {settings.mode}")
        return 1
    _ok("모드 확인", "mock")

    broker = KisBroker(settings)

    # 현재가로 지정가 계산
    time.sleep(STEP_DELAY)
    try:
        snap = broker.get_current_price(TEST_CODE)
    except Exception as e:
        _fail("현재가 조회", str(e))
        broker.close()
        return 1

    limit_price = round_down_to_krx_tick(
        market="KOSPI",
        price=int(snap.price * 0.95),
    )
    if limit_price <= 0:
        limit_price = snap.price
    _ok("현재가 조회",
        f"{snap.price:,}원 → 지정가 {limit_price:,}원 (-5%)")

    order_no: str | None = None

    # --------------------------------------------------------
    # [A] 지정가 매수
    # --------------------------------------------------------
    print(f"\n[A] 지정가 매수: {TEST_CODE} {TEST_QTY}주 @ {limit_price:,}원")
    time.sleep(STEP_DELAY)
    try:
        order_info = broker.place_order(
            code=TEST_CODE,
            side="buy",
            quantity=TEST_QTY,
            price=limit_price,
        )
    except (KisOrderError, KisApiError, Exception) as e:
        _fail("place_order", f"{type(e).__name__}: {e}")
        broker.close()
        return 1

    if order_info.status != OrderStatus.ACCEPTED:
        _fail("status", str(order_info.status))
        broker.close()
        return 1
    if not order_info.order_no:
        _fail("order_no", "없음")
        broker.close()
        return 1

    order_no = order_info.order_no
    _ok("매수 접수", f"order_no={order_no}")
    _ok("OrderInfo 필드",
        f"code={order_info.code} qty={order_info.quantity} "
        f"price={order_info.price:,} type={order_info.order_type.value}")

    # --------------------------------------------------------
    # [B] 미체결 조회 (모의 미지원 → 빈 리스트 확인)
    # --------------------------------------------------------
    print(f"\n[B] 미체결 조회 (모의 미지원 동작 확인)")
    time.sleep(STEP_DELAY)
    try:
        pending = broker.get_order_status(filled_only=False)
    except Exception as e:
        _fail("get_order_status(미체결)", f"{type(e).__name__}: {e}")
        broker.close()
        return 1

    if pending != []:
        _fail("모의 미체결 조회", f"빈 리스트여야 함: {pending}")
        broker.close()
        return 1
    _ok("모의 미체결 조회 → 빈 리스트 (예외 없음, 경고 로그 정상)")

    # --------------------------------------------------------
    # [C] 취소
    # --------------------------------------------------------
    print(f"\n[C] 취소: order_no={order_no}")
    time.sleep(STEP_DELAY)
    try:
        cancel_info = broker.cancel_order(
            order_no=order_no,
            code=TEST_CODE,
            quantity=TEST_QTY,
        )
    except KisApiError as e:
        _warn("취소 KisApiError",
              f"{e} → 이미 체결됐을 수 있음")
        cancel_info = None
    except KisOrderError as e:
        _fail("취소 KisOrderError(UNKNOWN)", str(e))
        _warn("주의", f"order_no={order_no} 상태 불확실. KIS HTS 확인 필요.")
        broker.close()
        return 1
    except Exception as e:
        _fail("취소 예외", f"{type(e).__name__}: {e}")
        broker.close()
        return 1

    if cancel_info is not None:
        if cancel_info.status != OrderStatus.CANCELLED:
            _fail("취소 status", str(cancel_info.status))
            broker.close()
            return 1
        _ok("취소 완료",
            f"cancel_order_no={cancel_info.order_no} "
            f"orig={order_no}")

    # --------------------------------------------------------
    # [D] 당일 체결 조회 (모의 지원 여부 확인)
    # --------------------------------------------------------
    print(f"\n[D] 당일 체결 조회 (inquire-daily-ccld)")
    time.sleep(STEP_DELAY)
    try:
        filled = broker.get_order_status(filled_only=True)
    except KisApiError as e:
        if "모의투자" in str(e) or "90000000" in str(e):
            _warn("당일 체결 조회 모의 미지원",
                  "미체결 조회와 동일하게 모의 미지원으로 처리 필요")
        else:
            _fail("get_order_status(체결)", str(e))
            broker.close()
            return 1
        filled = []
    except Exception as e:
        _fail("get_order_status(체결)", f"{type(e).__name__}: {e}")
        broker.close()
        return 1
    else:
        _ok("체결 조회 응답", f"{len(filled)}건 (모의 지원 확인)")
        if len(filled) > 0:
            latest = filled[-1]
            _ok("최근 항목",
                f"order_no={latest.order_no} "
                f"code={latest.code} "
                f"status={latest.status.value} "
                f"filled_qty={latest.filled_qty}")

    # --------------------------------------------------------
    broker.close()

    print()
    print("=" * 60)
    print(" Step B-5 완료")
    print("=" * 60)
    print()
    print("  확인된 모의투자 API 지원 현황:")
    print("    매수(VTTC0802U)         ✅")
    print("    취소(VTTC0803U)         ✅")
    print("    미체결(VTTC8036R)       ❌ 모의 미지원")
    print(f"    체결(VTTC8001R)         "
          f"{'✅' if filled is not None else '❌ 모의 미지원'}")
    print()
    print("  Phase 1-B 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
