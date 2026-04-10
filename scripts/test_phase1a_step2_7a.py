"""
Phase 1-A Step 2-7-A 검증.

목적:
    parsers.py + quote.py 동작 검증.
    - 단위 테스트 (가짜 KisResponse)
    - 실제 KIS 호출 (삼성전자 현재가)

실행:
    python scripts/test_phase1a_step2_7a.py
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
    print(" Phase 1-A Step 2-7-A 검증")
    print("=" * 60)

    print("\n[0] 준비")
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import (
        KisAuth, KisClient, KisResponse, PriceSnapshot, Quote,
    )
    from broker.kis.errors import KisParseError
    from broker.kis.parsers import (
        parse_price_snapshot, _to_int, _to_float,
    )

    settings = load_settings()
    setup_logging(settings)
    if settings.mode != "mock":
        _fail("mode", "mock 전용")
        return 1
    _ok("준비 완료")

    # ------------------------------------------------------------
    # 1. _to_int / _to_float 변환 헬퍼
    # ------------------------------------------------------------
    print("\n[1] 변환 헬퍼")
    cases_int = [
        ("70000", 70000),
        ("70000.00", 70000),
        ("1,234,567", 1234567),
        ("-500", -500),
        ("", 0),
        (None, 0),
        ("0", 0),
    ]
    for raw, expected in cases_int:
        actual = _to_int(raw, "test")
        if actual != expected:
            _fail(f"_to_int({raw!r})", f"기대={expected}, 실제={actual}")
            return 1
    _ok("_to_int", f"{len(cases_int)}케이스 통과")

    cases_float = [
        ("0.29", 0.29),
        ("-1.5", -1.5),
        ("", 0.0),
        (None, 0.0),
    ]
    for raw, expected in cases_float:
        actual = _to_float(raw, "test")
        if abs(actual - expected) > 1e-9:
            _fail(f"_to_float({raw!r})", f"기대={expected}, 실제={actual}")
            return 1
    _ok("_to_float", f"{len(cases_float)}케이스 통과")

    # 잘못된 값
    try:
        _to_int("abc", "test")
        _fail("_to_int('abc')", "예외 안 발생")
        return 1
    except KisParseError:
        _ok("_to_int('abc') → KisParseError")

    # ------------------------------------------------------------
    # 2. parse_price_snapshot - 정상 응답
    # ------------------------------------------------------------
    print("\n[2] parse_price_snapshot (정상)")
    fake_resp = KisResponse(
        body={
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output": {
                "stck_prpr": "70000",
                "stck_oprc": "69500",
                "stck_hgpr": "70500",
                "stck_lwpr": "69000",
                "stck_sdpr": "69800",
                "prdy_vrss": "200",
                "prdy_ctrt": "0.29",
                "acml_vol": "12345678",
            },
        },
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="FHKST01010100", http_status=200,
    )
    snap = parse_price_snapshot(fake_resp, "005930")

    if not isinstance(snap, PriceSnapshot):
        _fail("타입", str(type(snap)))
        return 1
    if snap.code != "005930":
        _fail("code", snap.code)
        return 1
    if snap.price != 70000:
        _fail("price", str(snap.price))
        return 1
    if snap.open != 69500 or snap.high != 70500 or snap.low != 69000:
        _fail("OHL", f"{snap.open}/{snap.high}/{snap.low}")
        return 1
    if snap.prev_close != 69800:
        _fail("prev_close", str(snap.prev_close))
        return 1
    if snap.change != 200:
        _fail("change", str(snap.change))
        return 1
    if abs(snap.change_rate - 0.29) > 1e-9:
        _fail("change_rate", str(snap.change_rate))
        return 1
    if snap.volume != 12345678:
        _fail("volume", str(snap.volume))
        return 1
    _ok("필드 매핑 정확", "9개 필드 모두 일치")

    # ------------------------------------------------------------
    # 3. parse_price_snapshot - 빈 output
    # ------------------------------------------------------------
    print("\n[3] parse_price_snapshot (빈 output)")
    empty_resp = KisResponse(
        body={"rt_cd": "0", "msg_cd": "X", "msg1": "OK", "output": {}},
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="FHKST01010100", http_status=200,
    )
    try:
        parse_price_snapshot(empty_resp, "999999")
        _fail("빈 output", "예외 안 발생")
        return 1
    except KisParseError as e:
        _ok("KisParseError", "빈 output 거부")

    # ------------------------------------------------------------
    # 4. Quote.get_current_price - 종목코드 검증
    # ------------------------------------------------------------
    print("\n[4] 종목코드 형식 검증")
    auth = KisAuth(settings)
    client = KisClient(settings, auth)
    quote = Quote(client)

    bad_codes = ["5930", "0059300", "abcdef", "", "00593O"]
    for bad in bad_codes:
        try:
            quote.get_current_price(bad)
            _fail(f"잘못된 코드 {bad!r}", "통과됨")
            return 1
        except ValueError:
            pass
    _ok("잘못된 코드 거부", f"{len(bad_codes)}케이스")

    # ------------------------------------------------------------
    # 5. Quote.get_current_price - 실제 호출 (삼성전자)
    # ------------------------------------------------------------
    print("\n[5] 삼성전자 현재가 (실제 KIS 호출)")
    try:
        snap = quote.get_current_price("005930")
    except Exception as e:
        _fail("실제 호출", f"{type(e).__name__}: {e}")
        return 1

    _ok("PriceSnapshot 반환")
    print(f"        code:        {snap.code}")
    print(f"        price:       {snap.price:,}원")
    print(f"        open/high/low: {snap.open:,} / {snap.high:,} / {snap.low:,}")
    print(f"        prev_close:  {snap.prev_close:,}")
    print(f"        change:      {snap.change:+,} ({snap.change_rate:+.2f}%)")
    print(f"        volume:      {snap.volume:,}")
    print(f"        timestamp:   {snap.timestamp}")

    # 합리성 검증
    if snap.price <= 0:
        _fail("price > 0", str(snap.price))
        return 1
    if snap.high < snap.low:
        _fail("high >= low", f"{snap.high} < {snap.low}")
        return 1
    _ok("합리성 검증", "price>0, high>=low")

    client.close()

    print()
    print("=" * 60)
    print(" Step 2-7-A 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-7-B (일봉/분봉 + DataFrame)")
    return 0


if __name__ == "__main__":
    sys.exit(main())