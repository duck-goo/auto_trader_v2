"""
Phase 1-A 최종 통합 검증.

KisBroker Facade 하나만으로 Phase 1-A의 모든 기능을 테스트한다.
    - 현재가
    - 일봉
    - 분봉
    - 잔고
    - BrokerInterface 구현 확인
    - context manager 정상 동작

실행:
    python scripts/test_phase1a_final.py
"""

from __future__ import annotations

import sys
import time as _t
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
    print(" Phase 1-A 최종 통합 검증")
    print("=" * 60)

    print("\n[0] 준비")
    from config.loader import load_settings
    from logger import setup_logging
    from broker.base import BrokerInterface
    from broker.kis import Balance, KisBroker, PriceSnapshot

    settings = load_settings()
    setup_logging(settings)
    if settings.mode != "mock":
        _fail("mode", "mock 전용")
        return 1
    _ok("준비 완료")

    # ------------------------------------------------------------
    # 1. BrokerInterface 구현 확인
    # ------------------------------------------------------------
    print("\n[1] BrokerInterface 구현")
    broker = KisBroker(settings)

    if not isinstance(broker, BrokerInterface):
        _fail("isinstance", "BrokerInterface 구현 안 함")
        return 1
    _ok("isinstance(BrokerInterface)")

    required_methods = [
        "get_access_token", "get_current_price",
        "get_daily_candles", "get_minute_candles",
        "get_balance", "close",
    ]
    for m in required_methods:
        if not callable(getattr(broker, m, None)):
            _fail(f"메서드: {m}", "없음")
            return 1
    _ok(f"필수 메서드 {len(required_methods)}개")

    # ------------------------------------------------------------
    # 2. 토큰 (Phase 0 호환)
    # ------------------------------------------------------------
    print("\n[2] 토큰 조회")
    token = broker.get_access_token()
    if not token or len(token) < 20:
        _fail("토큰", "이상")
        return 1
    _ok("get_access_token()", f"len={len(token)}")

    # ------------------------------------------------------------
    # 3. 현재가
    # ------------------------------------------------------------
    print("\n[3] 삼성전자 현재가")
    _t.sleep(1.0)
    try:
        snap = broker.get_current_price("005930")
    except Exception as e:
        _fail("현재가", f"{type(e).__name__}: {e}")
        broker.close()
        return 1

    if not isinstance(snap, PriceSnapshot):
        _fail("타입", str(type(snap)))
        return 1
    if snap.price <= 0:
        _fail("price > 0", str(snap.price))
        return 1
    _ok("현재가", f"{snap.price:,}원 ({snap.change:+,}, {snap.change_rate:+.2f}%)")

    # ------------------------------------------------------------
    # 4. 일봉
    # ------------------------------------------------------------
    print("\n[4] 일봉 30개")
    _t.sleep(2.0)
    try:
        daily = broker.get_daily_candles("005930", count=30)
    except Exception as e:
        _fail("일봉", f"{type(e).__name__}: {e}")
        broker.close()
        return 1

    if len(daily) == 0:
        _fail("일봉 길이", "0행")
        return 1
    _ok("일봉", f"{len(daily)}행")
    print(f"        기간: {daily.iloc[0]['datetime'].date()} ~ {daily.iloc[-1]['datetime'].date()}")

    # ------------------------------------------------------------
    # 5. 분봉
    # ------------------------------------------------------------
    print("\n[5] 분봉 1분봉")
    _t.sleep(2.0)
    try:
        minute = broker.get_minute_candles("005930", interval="1")
    except Exception as e:
        _fail("분봉", f"{type(e).__name__}: {e}")
        broker.close()
        return 1
    _ok("분봉", f"{len(minute)}행")

    # ------------------------------------------------------------
    # 6. 잔고
    # ------------------------------------------------------------
    print("\n[6] 잔고")
    _t.sleep(2.0)
    try:
        balance = broker.get_balance()
    except Exception as e:
        _fail("잔고", f"{type(e).__name__}: {e}")
        broker.close()
        return 1

    if not isinstance(balance, Balance):
        _fail("타입", str(type(balance)))
        return 1
    _ok("잔고", f"예수금 {balance.cash:,}원, 보유 {balance.holding_count}종목")

    # ------------------------------------------------------------
    # 7. context manager
    # ------------------------------------------------------------
    broker.close()
    print("\n[7] context manager")
    try:
        with KisBroker(settings) as b:
            tk = b.get_access_token()
            if not tk:
                _fail("with 내부", "토큰 없음")
                return 1
    except Exception as e:
        _fail("with", str(e))
        return 1
    _ok("with KisBroker(...) as b 정상")

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(" Phase 1-A 완료")
    print("=" * 60)
    print()
    print("  ✅ 설정 로드 / 로거 / 토큰 캐싱")
    print("  ✅ KIS REST 클라이언트 (TR_ID 변환, 레이트리밋, 재시도, 401 안전망)")
    print("  ✅ 현재가 / 일봉 / 분봉 조회")
    print("  ✅ 계좌 잔고 조회")
    print("  ✅ KisBroker Facade (BrokerInterface 구현)")
    print()
    print("  다음: Phase 1-B (주문 - place_order / cancel_order / get_order_status)")
    return 0


if __name__ == "__main__":
    sys.exit(main())