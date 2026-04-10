"""
Phase 1-A Step 1 검증.

목적:
    Step 1에서 추가한 파일들이 import 에러 없이 로드되고,
    KisAuth가 Lock 추가 후에도 Phase 0과 동일하게 동작하는지 확인.

이 단계에서는 네트워크 호출하지 않는다.
KIS API 호출은 Step 2(client.py) 이후 검증.

실행:
    cd C:\\python\\auto_trader_v2
    python scripts/test_phase1a_step1.py
"""

from __future__ import annotations

import sys
import threading
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
    print(" Phase 1-A Step 1 검증")
    print("=" * 60)

    # ------------------------------------------------------------
    # 1. errors.py import 및 계층 검증
    # ------------------------------------------------------------
    print("\n[1] 예외 계층")
    try:
        from broker.kis.errors import (
            KisError,
            KisAuthError,
            KisApiError,
            KisParseError,
            KisRateLimitError,
            TOKEN_EXPIRED_MSG_CODES,
        )
    except ImportError as e:
        _fail("errors.py import", str(e))
        return 1
    _ok("errors.py import")

    # 상속 관계
    for cls in (KisAuthError, KisApiError, KisParseError, KisRateLimitError):
        if not issubclass(cls, KisError):
            _fail(f"{cls.__name__} ⊂ KisError", "상속 관계 깨짐")
            return 1
    _ok("모든 예외가 KisError 하위")

    # KisApiError 필드 동작
    err = KisApiError(
        "테스트", rt_cd="1", msg_cd="EGW00123",
        msg="만료", http_status=200, tr_id="FHKST01010100",
    )
    s = str(err)
    for needle in ("EGW00123", "FHKST01010100", "rt_cd=1", "http=200"):
        if needle not in s:
            _fail("KisApiError __str__", f"{needle!r} 누락: {s}")
            return 1
    _ok("KisApiError __str__", "필드 모두 출력")

    if not isinstance(TOKEN_EXPIRED_MSG_CODES, frozenset):
        _fail("TOKEN_EXPIRED_MSG_CODES 타입", "frozenset 아님")
        return 1
    # 공식 샘플 분석 결과: 시간 기반 만료 체크가 정답이므로 비어있어야 함.
    # 추후 운영 중 실측으로 정확한 코드 발견 시에만 추가한다.
    if len(TOKEN_EXPIRED_MSG_CODES) != 0:
        _fail(
            "TOKEN_EXPIRED_MSG_CODES 빈 집합",
            f"{len(TOKEN_EXPIRED_MSG_CODES)}개 (추정값 박혀있음, errors.py 확인)",
        )
        return 1
    _ok("TOKEN_EXPIRED_MSG_CODES", "비어있음 (시간 기반 만료 체크에 의존)")

    # ------------------------------------------------------------
    # 2. models.py import 및 dataclass 동작
    # ------------------------------------------------------------
    print("\n[2] 데이터 모델")
    try:
        from datetime import datetime
        import pytz

        from broker.kis.models import Balance, Holding, PriceSnapshot
    except ImportError as e:
        _fail("models.py import", str(e))
        return 1
    _ok("models.py import")

    KST = pytz.timezone("Asia/Seoul")
    snap = PriceSnapshot(
        code="005930", name="삼성전자",
        price=70000, open=69500, high=70500, low=69000,
        prev_close=69800, change=200, change_rate=0.29,
        volume=1234567, timestamp=datetime.now(KST),
    )
    # frozen 검증
    try:
        snap.price = 71000  # type: ignore[misc]
        _fail("PriceSnapshot frozen", "변경이 허용됨")
        return 1
    except Exception:
        _ok("PriceSnapshot frozen", "변경 차단")

    # Holding + Balance.find
    h1 = Holding(
        code="005930", name="삼성전자",
        quantity=10, available=10, avg_price=68000.0,
        current_price=70000, eval_amount=700000,
        profit=20000, profit_rate=2.94,
    )
    h2 = Holding(
        code="000660", name="SK하이닉스",
        quantity=5, available=5, avg_price=120000.0,
        current_price=125000, eval_amount=625000,
        profit=25000, profit_rate=4.17,
    )
    bal = Balance(
        cash=1_000_000, available_cash=950_000,
        total_eval=2_325_000, total_profit=45_000,
        holdings=(h1, h2),
        timestamp=datetime.now(KST),
    )
    if bal.find("005930") is not h1:
        _fail("Balance.find", "정확한 인스턴스 미반환")
        return 1
    if bal.find("999999") is not None:
        _fail("Balance.find(없는코드)", "None이 아님")
        return 1
    if bal.holding_count != 2:
        _fail("Balance.holding_count", str(bal.holding_count))
        return 1
    _ok("Balance.find / holding_count")

    # ------------------------------------------------------------
    # 3. broker.kis 패키지 export 검증
    # ------------------------------------------------------------
    print("\n[3] 패키지 export")
    try:
        import broker.kis as kis_pkg
    except ImportError as e:
        _fail("broker.kis import", str(e))
        return 1

    expected = {
        "KisAuth", "KisClient",
        "KisError", "KisAuthError", "KisApiError",
        "KisParseError", "KisRateLimitError",
        "Balance", "Holding", "KisResponse", "PriceSnapshot",
        "Quote",
    }
    missing = expected - set(dir(kis_pkg))
    if missing:
        _fail("export", f"누락: {missing}")
        return 1
    _ok("export", f"{len(expected)}개 최소 보장 (실제 더 있을 수 있음)")

    # 호환성: 기존 경로 from broker.kis.auth import KisAuthError
    try:
        from broker.kis.auth import KisAuthError as KAE_legacy
        if KAE_legacy is not KisAuthError:
            _fail("legacy import 호환", "다른 클래스")
            return 1
    except ImportError as e:
        _fail("legacy import 호환", str(e))
        return 1
    _ok("legacy import 호환", "broker.kis.auth.KisAuthError 동일")

    # ------------------------------------------------------------
    # 4. KisAuth Lock 동작 (네트워크 없이)
    # ------------------------------------------------------------
    print("\n[4] KisAuth 동시성 (네트워크 미사용)")
    try:
        from config.loader import load_settings
        from broker.kis import KisAuth
    except ImportError as e:
        _fail("KisAuth import", str(e))
        return 1

    try:
        settings = load_settings()
    except Exception as e:
        _fail("settings 로드", str(e))
        return 1

    auth = KisAuth(settings)

    if not hasattr(auth, "_lock"):
        _fail("Lock 속성", "_lock 없음")
        return 1
    _ok("_lock 속성 존재", type(auth._lock).__name__)

    # 캐시된 토큰이 있다면 그대로 사용 (네트워크 미발생).
    # 캐시가 없으면 이 단계는 건너뛴다 (Step 1은 네트워크 검증 아님).
    cache_file = settings.token_cache_dir / f"kis_{settings.mode}.json"
    if cache_file.exists():
        # 멀티스레드에서 동시 호출 → 모두 같은 토큰 받아야 함
        results: list[str] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                results.append(auth.get_access_token())
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if errors:
            _fail("멀티스레드 토큰 조회", str(errors[0]))
            return 1
        if len(set(results)) != 1:
            _fail("멀티스레드 토큰 일관성", f"{len(set(results))}종류 반환")
            return 1
        _ok("멀티스레드 토큰 일관성", f"5개 호출 모두 동일")
    else:
        print("  [SKIP] 토큰 캐시 없음 → 네트워크 회피 위해 동시성 실호출 생략")
        print("         (Phase 0 test_auth.py를 먼저 실행하면 이 검증도 동작)")

    # ------------------------------------------------------------
    # 5. pandas 설치 확인 (Step 3 사전점검)
    # ------------------------------------------------------------
    print("\n[5] pandas 설치 확인")
    try:
        import pandas as pd
        _ok("pandas import", f"v{pd.__version__}")
    except ImportError:
        _fail("pandas import", "pip install -r requirements.txt 실행 필요")
        return 1

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step 1 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2 (broker/kis/client.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())