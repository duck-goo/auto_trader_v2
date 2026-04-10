"""
KIS 인증 통합 테스트 (수동 실행).

실제 KIS 모의투자 서버에 접속해서 토큰 발급/캐싱/갱신이
정상 동작하는지 확인한다.

실행:
    cd C:\\python\\auto_trader_v2
    python scripts/test_auth.py

확인 항목:
    1. 신규 발급 (파일 캐시 없는 상태)
    2. 메모리 캐시 재사용
    3. 파일 캐시 재사용 (메모리 캐시 제거 후)
    4. 강제 재발급 (force_refresh)
    5. 캐시 파일 내용 검증
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (scripts에서 실행 시 필요)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.kis import KisAuth, KisAuthError  # noqa: E402
from config.loader import load_settings, SettingsError  # noqa: E402
from logger import get_logger, setup_logging  # noqa: E402


def _print_section(title: str) -> None:
    """섹션 구분선 출력."""
    print()
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)


def _print_result(label: str, ok: bool, detail: str = "") -> None:
    """결과 출력."""
    mark = "[ OK ]" if ok else "[FAIL]"
    line = f"  {mark} {label}"
    if detail:
        line += f" - {detail}"
    print(line)


def _mask_token(token: str) -> str:
    """토큰 일부만 표시 (로그 유출 방지)."""
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...{token[-4:]} (len={len(token)})"


def main() -> int:
    """
    Returns:
        0 성공 / 1 실패
    """
    log = get_logger("system")

    # ------------------------------------------------------------
    # 0. 설정 로드 + 로거 초기화
    # ------------------------------------------------------------
    _print_section("0. 설정 로드")
    try:
        settings = load_settings()
    except SettingsError as e:
        print(f"  [FAIL] 설정 로드 실패: {e}")
        return 1

    setup_logging(settings)
    _print_result("설정 로드", True, f"mode={settings.mode}")
    _print_result(
        "토큰 캐시 경로",
        True,
        str(settings.token_cache_dir),
    )

    if settings.mode != "mock":
        print("  [FAIL] 이 테스트는 mock 모드에서만 실행 가능합니다.")
        return 1

    auth = KisAuth(settings)
    cache_file = settings.token_cache_dir / f"kis_{settings.mode}.json"

    # ------------------------------------------------------------
    # 1. 신규 발급 (기존 캐시 제거)
    # ------------------------------------------------------------
    _print_section("1. 신규 발급 테스트")
    if cache_file.exists():
        print(f"  기존 캐시 파일 삭제: {cache_file.name}")
        cache_file.unlink()

    # 메모리 캐시도 비우기 (안전장치)
    auth._memory_cache = None  # type: ignore[attr-defined]

    try:
        start = time.perf_counter()
        token1 = auth.get_access_token()
        elapsed = time.perf_counter() - start
    except KisAuthError as e:
        _print_result("신규 발급", False, str(e))
        log.error(f"신규 발급 실패: {e}")
        return 1

    _print_result(
        "신규 발급",
        True,
        f"{_mask_token(token1)}, {elapsed:.2f}초",
    )

    if not cache_file.exists():
        _print_result("캐시 파일 생성", False, "파일이 생성되지 않음")
        return 1
    _print_result("캐시 파일 생성", True, cache_file.name)

    # ------------------------------------------------------------
    # 2. 메모리 캐시 재사용
    # ------------------------------------------------------------
    _print_section("2. 메모리 캐시 재사용")
    start = time.perf_counter()
    token2 = auth.get_access_token()
    elapsed = time.perf_counter() - start

    same = token1 == token2
    fast = elapsed < 0.1  # 네트워크 안 탔으면 10ms 이내

    _print_result(
        "동일 토큰 반환",
        same,
        "같음" if same else "다름 (비정상)",
    )
    _print_result(
        "즉시 반환 (네트워크 미사용)",
        fast,
        f"{elapsed * 1000:.1f}ms",
    )

    if not (same and fast):
        return 1

    # ------------------------------------------------------------
    # 3. 파일 캐시 재사용 (메모리 캐시 제거 후)
    # ------------------------------------------------------------
    _print_section("3. 파일 캐시 재사용")
    auth._memory_cache = None  # type: ignore[attr-defined]

    start = time.perf_counter()
    token3 = auth.get_access_token()
    elapsed = time.perf_counter() - start

    same = token1 == token3
    fast = elapsed < 1.0  # 파일 읽기는 네트워크보다 훨씬 빠름

    _print_result(
        "동일 토큰 반환",
        same,
        "같음" if same else "다름 (비정상)",
    )
    _print_result(
        "파일 캐시 사용",
        fast,
        f"{elapsed * 1000:.1f}ms",
    )

    if not (same and fast):
        return 1

    # ------------------------------------------------------------
    # 4. 캐시 파일 내용 검증
    # ------------------------------------------------------------
    _print_section("4. 캐시 파일 내용 검증")
    import json

    try:
        with cache_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _print_result("파일 읽기", False, str(e))
        return 1

    required_keys = {
        "access_token",
        "token_type",
        "issued_at",
        "expires_at",
        "mode",
    }
    missing = required_keys - set(data.keys())
    if missing:
        _print_result("필수 필드", False, f"누락: {missing}")
        return 1
    _print_result("필수 필드", True, "모두 존재")

    mode_ok = data["mode"] == "mock"
    _print_result("모드 일치", mode_ok, data["mode"])

    # issued_at < expires_at 확인
    from datetime import datetime

    try:
        issued = datetime.fromisoformat(data["issued_at"])
        expires = datetime.fromisoformat(data["expires_at"])
    except ValueError as e:
        _print_result("시각 파싱", False, str(e))
        return 1

    order_ok = issued < expires
    duration = expires - issued
    _print_result(
        "시각 순서 (issued < expires)",
        order_ok,
        f"유효기간 {duration}",
    )

    if not (mode_ok and order_ok):
        return 1

    # ------------------------------------------------------------
    # 5. 결과 요약
    # ------------------------------------------------------------
    _print_section("Phase 0 Group 4 테스트 결과")
    print("  [ OK ] 1. 신규 발급")
    print("  [ OK ] 2. 메모리 캐시 재사용")
    print("  [ OK ] 3. 파일 캐시 재사용")
    print("  [ OK ] 4. 캐시 파일 내용 검증")
    print()
    print("  Phase 0 완료. Phase 1(시세/잔고 조회)으로 진행 가능.")
    print()
    print(f"  토큰 캐시: {cache_file}")
    print(f"  만료 시각: {data['expires_at']}")
    print()

    # 주의: 강제 재발급(force_refresh) 테스트는 생략.
    # KIS 정책상 1분 1회 제한이 있어서 연달아 발급 시 실패 위험.
    # 필요 시 수동으로 별도 실행.

    return 0


if __name__ == "__main__":
    sys.exit(main())