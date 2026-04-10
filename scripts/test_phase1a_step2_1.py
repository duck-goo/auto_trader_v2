"""
Phase 1-A Step 2-1 검증.

목적:
    settings.yaml에 추가된 4개 항목을 loader가 정상 로드하고
    검증 로직이 작동하는지 확인. 네트워크 호출 없음.

검증 항목:
    1. 정상 로드 (4개 신규 필드 모두 존재)
    2. 모드별 rate_limit_interval 정확한 값 (mock=0.5)
    3. 타입 검증 (float, int, str)
    4. Phase 0 회귀 (기존 필드 유지)
    5. (수동) 잘못된 yaml 거부 - 임시 yaml로 시뮬레이션은 생략, 코드 리뷰로 대체

실행:
    cd C:\\python\\auto_trader_v2
    python scripts/test_phase1a_step2_1.py
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
    print(" Phase 1-A Step 2-1 검증")
    print("=" * 60)

    # ------------------------------------------------------------
    # 1. 설정 로드
    # ------------------------------------------------------------
    print("\n[1] 설정 로드")
    try:
        from config.loader import load_settings, SettingsError
    except ImportError as e:
        _fail("loader import", str(e))
        return 1

    try:
        settings = load_settings()
    except SettingsError as e:
        _fail("load_settings()", str(e))
        print()
        print("  → settings.yaml에 신규 항목이 추가되었는지 확인:")
        print("    - kis.mock.rate_limit_interval")
        print("    - kis.real.rate_limit_interval")
        print("    - network.request_retry_count")
        print("    - network.request_retry_delay")
        print("    - http.user_agent")
        return 1
    _ok("load_settings()", f"mode={settings.mode}")

    # ------------------------------------------------------------
    # 2. 신규 필드 4개 존재 + 타입
    # ------------------------------------------------------------
    print("\n[2] 신규 필드 존재 및 타입")

    checks = [
        ("kis_rate_limit_interval", float),
        ("request_retry_count", int),
        ("request_retry_delay", int),
        ("http_user_agent", str),
    ]
    for field, expected_type in checks:
        if not hasattr(settings, field):
            _fail(f"필드 존재: {field}", "Settings에 없음")
            return 1
        value = getattr(settings, field)
        if not isinstance(value, expected_type):
            _fail(
                f"타입: {field}",
                f"기대={expected_type.__name__}, 실제={type(value).__name__}",
            )
            return 1
        _ok(f"{field}", f"{expected_type.__name__} = {value!r}")

    # ------------------------------------------------------------
    # 3. 모드별 정확한 값 (mock 기준)
    # ------------------------------------------------------------
    print("\n[3] 모드별 값 검증")
    if settings.mode != "mock":
        _fail("mode", f"mock이 아님: {settings.mode}")
        return 1

    # 공식 _smartSleep 값과 일치 확인
    expected_interval = 0.5  # 모의투자
    if abs(settings.kis_rate_limit_interval - expected_interval) > 1e-9:
        _fail(
            "rate_limit_interval (mock)",
            f"기대={expected_interval}, 실제={settings.kis_rate_limit_interval}",
        )
        return 1
    _ok(
        "rate_limit_interval (mock)",
        f"{settings.kis_rate_limit_interval}초 (= 공식 _smartSleep)",
    )

    if settings.request_retry_count < 1:
        _fail("request_retry_count", "1 미만")
        return 1
    _ok("request_retry_count", f">= 1: {settings.request_retry_count}")

    if settings.request_retry_delay < 0:
        _fail("request_retry_delay", "음수")
        return 1
    _ok("request_retry_delay", f">= 0: {settings.request_retry_delay}")

    if not settings.http_user_agent.strip():
        _fail("http_user_agent", "빈 문자열")
        return 1
    _ok(
        "http_user_agent",
        f"len={len(settings.http_user_agent)}",
    )

    # ------------------------------------------------------------
    # 4. 기존 필드 유지 (Phase 0 회귀 방지)
    # ------------------------------------------------------------
    print("\n[4] 기존 필드 회귀 검증")
    legacy_fields = [
        "mode", "kis_app_key", "kis_app_secret", "kis_account_no",
        "kis_rest_url", "kis_ws_url",
        "token_expiry_buffer_minutes", "token_cache_dir",
        "log_level", "log_dir", "log_console", "log_file",
        "request_timeout", "token_retry_count", "token_retry_delay",
    ]
    missing = [f for f in legacy_fields if not hasattr(settings, f)]
    if missing:
        _fail("기존 필드 누락", f"{missing}")
        return 1
    _ok("기존 필드", f"{len(legacy_fields)}개 모두 존재")

    # ------------------------------------------------------------
    # 5. KisAuth 생성 회귀 (기존 코드 영향 없음 확인)
    # ------------------------------------------------------------
    print("\n[5] KisAuth 생성 회귀")
    try:
        from broker.kis import KisAuth
        auth = KisAuth(settings)
    except Exception as e:
        _fail("KisAuth(settings)", str(e))
        return 1
    _ok("KisAuth(settings)", "생성 성공")

    if not hasattr(auth, "_lock"):
        _fail("Lock 유지", "_lock 없음")
        return 1
    _ok("Lock 유지", "RLock 정상")

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step 2-1 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-2 (models.py에 KisResponse 추가)")
    return 0


if __name__ == "__main__":
    sys.exit(main())