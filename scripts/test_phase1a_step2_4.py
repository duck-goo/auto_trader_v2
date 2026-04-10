"""
Phase 1-A Step 2-4 검증.

목적:
    _enforce_rate_limit이 모드별 최소 호출 간격을 정확히 보장하는지 확인.
    네트워크 호출 없음.

검증 항목:
    1. 단일 스레드 연속 호출 — 간격이 0.5초 이상인지
    2. 멀티스레드 동시 호출 — 모두 직렬화되어 0.5초 간격 유지
    3. interval=0 시 즉시 통과 (비활성화)

실행:
    python scripts/test_phase1a_step2_4.py
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import replace
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
    print(" Phase 1-A Step 2-4 검증")
    print("=" * 60)

    # ------------------------------------------------------------
    # 0. 준비
    # ------------------------------------------------------------
    print("\n[0] 준비")
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisAuth, KisClient

    settings = load_settings()
    setup_logging(settings)
    auth = KisAuth(settings)
    client = KisClient(settings, auth)

    interval = settings.kis_rate_limit_interval
    _ok("준비 완료", f"interval={interval}s")

    # ------------------------------------------------------------
    # 1. 단일 스레드 연속 3회 호출 — 간격 확인
    # ------------------------------------------------------------
    print("\n[1] 단일 스레드 연속 호출")

    # _last_call_at 초기화 (첫 호출은 즉시 통과하도록)
    client._last_call_at = 0.0

    timestamps: list[float] = []
    for i in range(3):
        client._enforce_rate_limit()
        timestamps.append(time.monotonic())

    # 첫 호출은 즉시 통과 (이전 호출이 0.0이므로)
    # 두 번째부터 interval 이상 간격이어야 함
    for i in range(1, len(timestamps)):
        gap = timestamps[i] - timestamps[i - 1]
        # 약간의 오차 허용 (sleep 정밀도 + OS 스케줄링)
        tolerance = 0.05
        if gap < (interval - tolerance):
            _fail(
                f"호출 {i} → {i + 1} 간격",
                f"{gap:.3f}s < {interval}s (최소 간격 미달)",
            )
            return 1

    gap1 = timestamps[1] - timestamps[0]
    gap2 = timestamps[2] - timestamps[1]
    _ok("3회 연속 호출", f"간격: {gap1:.3f}s, {gap2:.3f}s (>= {interval}s)")

    # ------------------------------------------------------------
    # 2. 멀티스레드 동시 호출 — 직렬화 확인
    # ------------------------------------------------------------
    print("\n[2] 멀티스레드 동시 호출 (5스레드)")

    client._last_call_at = 0.0
    mt_timestamps: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        client._enforce_rate_limit()
        t = time.monotonic()
        with lock:
            mt_timestamps.append(t)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.monotonic() - start

    # 5회 호출이 직렬화되면 최소 (5-1) * interval = 2.0초 소요 (mock 기준)
    # 첫 호출은 즉시 통과 가능하므로 4 * interval
    min_expected_total = (len(threads) - 1) * interval
    # 약간의 여유 (-0.1초)
    if total < min_expected_total - 0.1:
        _fail(
            "총 소요시간",
            f"{total:.2f}s < {min_expected_total:.1f}s (직렬화 안 됨)",
        )
        return 1
    _ok(
        "직렬화 확인",
        f"총 {total:.2f}s >= {min_expected_total:.1f}s (5스레드)",
    )

    # 각 호출 간 간격도 확인
    mt_timestamps.sort()
    all_gaps_ok = True
    gap_details: list[str] = []
    tolerance = 0.05
    for i in range(1, len(mt_timestamps)):
        gap = mt_timestamps[i] - mt_timestamps[i - 1]
        gap_details.append(f"{gap:.3f}s")
        if gap < (interval - tolerance):
            all_gaps_ok = False

    if not all_gaps_ok:
        _fail("개별 간격", f"간격들: {', '.join(gap_details)}")
        return 1
    _ok("개별 간격", f"{', '.join(gap_details)} (모두 >= {interval}s)")

    # ------------------------------------------------------------
    # 3. interval=0 비활성화 테스트
    # ------------------------------------------------------------
    print("\n[3] interval=0 (레이트리밋 비활성화)")
    zero_settings = replace(settings, kis_rate_limit_interval=0.0)
    zero_client = KisClient(zero_settings, auth)

    zero_start = time.monotonic()
    for _ in range(10):
        zero_client._enforce_rate_limit()
    zero_elapsed = time.monotonic() - zero_start

    zero_client.close()

    # 10회 호출이 0.1초 이내면 비활성화 정상
    if zero_elapsed > 0.1:
        _fail("비활성화", f"{zero_elapsed:.3f}s (0.1초 초과)")
        return 1
    _ok("비활성화", f"10회 호출 {zero_elapsed:.4f}s (<0.1s)")

    # 정리
    client.close()

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step 2-4 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-5 (_validate_response)")
    print()
    print(f" 참고: 이 테스트는 약 {min_expected_total:.0f}초 소요됩니다")
    print(f"       (5스레드 × {interval}s 간격 = 직렬화 대기)")
    return 0


if __name__ == "__main__":
    sys.exit(main())