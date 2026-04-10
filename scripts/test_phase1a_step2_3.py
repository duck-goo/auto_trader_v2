"""
Phase 1-A Step 2-3 검증.

목적:
    KisClient 골격이 정상 동작하는지 확인.
    네트워크 호출 없음. 토큰 발급도 (가능하면) 캐시 사용.

검증 항목:
    1. import / export
    2. 인스턴스 생성 + 라이프사이클 (close, context manager)
    3. _resolve_tr_id (10가지 케이스 - 가장 중요)
    4. _build_headers (필수 필드, 값, extra 병합)
    5. _mask_secret (민감 헤더 마스킹, 원본 불변)
    6. NotImplementedError 발생 (아직 미구현 메서드)

실행:
    python scripts/test_phase1a_step2_3.py
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
    print(" Phase 1-A Step 2-3 검증")
    print("=" * 60)

    # ------------------------------------------------------------
    # 0. 사전 준비
    # ------------------------------------------------------------
    print("\n[0] 사전 준비")
    try:
        from config.loader import load_settings
        from logger import setup_logging
        from broker.kis import KisAuth, KisClient
    except ImportError as e:
        _fail("import", str(e))
        return 1

    try:
        settings = load_settings()
    except Exception as e:
        _fail("load_settings", str(e))
        return 1
    setup_logging(settings)
    _ok("settings + logger", f"mode={settings.mode}")

    if settings.mode != "mock":
        _fail("mode", "이 테스트는 mock 모드 전용")
        return 1

    auth = KisAuth(settings)
    _ok("KisAuth 생성")

    # ------------------------------------------------------------
    # 1. import / export
    # ------------------------------------------------------------
    print("\n[1] import / export")
    import broker.kis as kis_pkg
    if "KisClient" not in dir(kis_pkg):
        _fail("KisClient export", "broker.kis에 노출 안 됨")
        return 1
    _ok("KisClient export")

    # ------------------------------------------------------------
    # 2. 인스턴스 생성 + 라이프사이클
    # ------------------------------------------------------------
    print("\n[2] 인스턴스 생성 / 라이프사이클")
    try:
        client = KisClient(settings, auth)
    except Exception as e:
        _fail("KisClient 생성", str(e))
        return 1
    _ok("KisClient(settings, auth)")

    # 필수 속성
    for attr in ("_settings", "_auth", "_session", "_rate_lock", "_last_call_at"):
        if not hasattr(client, attr):
            _fail(f"속성: {attr}", "없음")
            return 1
    _ok("필수 내부 속성 5개 존재")

    # close 가능
    try:
        client.close()
    except Exception as e:
        _fail("close()", str(e))
        return 1
    _ok("close()")

    # context manager
    try:
        with KisClient(settings, auth) as c:
            assert c is not None
    except Exception as e:
        _fail("context manager", str(e))
        return 1
    _ok("context manager (with)")

    # 새 인스턴스 (이후 테스트용)
    client = KisClient(settings, auth)

    # ------------------------------------------------------------
    # 3. _resolve_tr_id (가장 중요 - 10가지 케이스)
    # ------------------------------------------------------------
    print("\n[3] _resolve_tr_id (모의투자 변환)")
    cases = [
        # (입력, 기대출력, 설명)
        ("TTTC0802U",     "VTTC0802U",     "T시작 매수 → V로 변환"),
        ("TTTC0801U",     "VTTC0801U",     "T시작 매도 → V로 변환"),
        ("TTTC8434R",     "VTTC8434R",     "T시작 잔고 → V로 변환"),
        ("JTCE6001R",     "VTCE6001R",     "J시작 → V로 변환"),
        ("CTPF1002R",     "VTPF1002R",     "C시작 → V로 변환"),
        ("FHKST01010100", "FHKST01010100", "F시작 시세 → 변환 안함"),
        ("FHKST03010200", "FHKST03010200", "F시작 분봉 → 변환 안함"),
        ("HHDFS00000300", "HHDFS00000300", "H시작 → 변환 안함"),
        ("VTTC0802U",     "VTTC0802U",     "이미 V → 변환 안함 (멱등)"),
        ("T",             "V",             "1글자 T → V (경계)"),
    ]
    for input_tr, expected, desc in cases:
        try:
            actual = client._resolve_tr_id(input_tr)
        except Exception as e:
            _fail(f"_resolve_tr_id({input_tr!r})", str(e))
            return 1
        if actual != expected:
            _fail(
                f"_resolve_tr_id({input_tr!r})",
                f"기대={expected!r}, 실제={actual!r} ({desc})",
            )
            return 1
    _ok("_resolve_tr_id (mock)", f"{len(cases)}케이스 통과")

    # 빈 문자열 거부
    try:
        client._resolve_tr_id("")
        _fail("_resolve_tr_id('')", "빈 문자열을 받았는데 통과함")
        return 1
    except ValueError:
        _ok("_resolve_tr_id('')", "ValueError 발생 (예상)")

    # ------------------------------------------------------------
    # 3-1. _resolve_tr_id (실전 모드 시뮬레이션)
    # ------------------------------------------------------------
    print("\n[3-1] _resolve_tr_id (실전 모드는 변환 안 함)")
    # settings는 frozen이라 dataclasses.replace 사용
    from dataclasses import replace
    real_settings = replace(settings, mode="real")
    real_client_dummy = KisClient.__new__(KisClient)  # __init__ 우회
    real_client_dummy._settings = real_settings  # type: ignore[attr-defined]
    for input_tr in ("TTTC0802U", "FHKST01010100", "JTCE6001R"):
        actual = real_client_dummy._resolve_tr_id(input_tr)
        if actual != input_tr:
            _fail(
                f"실전 모드 _resolve_tr_id({input_tr!r})",
                f"실제={actual!r} (변환되면 안 됨)",
            )
            return 1
    _ok("_resolve_tr_id (real)", "3케이스 모두 변환 없음")

    # ------------------------------------------------------------
    # 4. _build_headers
    # ------------------------------------------------------------
    print("\n[4] _build_headers")
    try:
        headers = client._build_headers(
            tr_id="FHKST01010100",
            tr_cont="",
        )
    except Exception as e:
        _fail("_build_headers 호출", str(e))
        print()
        print("  → 토큰 캐시가 없으면 KIS API 호출이 발생합니다.")
        print("    test_auth.py를 먼저 실행해서 캐시를 만들어주세요.")
        return 1

    required = {
        "Content-Type", "Accept", "charset", "User-Agent",
        "authorization", "appkey", "appsecret",
        "tr_id", "tr_cont", "custtype",
    }
    missing = required - set(headers.keys())
    if missing:
        _fail("필수 헤더", f"누락: {missing}")
        return 1
    _ok("필수 헤더 10개 존재")

    # 값 검증
    if not headers["authorization"].startswith("Bearer "):
        _fail("authorization 형식", headers["authorization"][:20])
        return 1
    _ok("authorization", "Bearer 접두사")

    if headers["tr_id"] != "FHKST01010100":
        _fail("tr_id", headers["tr_id"])
        return 1
    if headers["custtype"] != "P":
        _fail("custtype", headers["custtype"])
        return 1
    if headers["Content-Type"] != "application/json":
        _fail("Content-Type", headers["Content-Type"])
        return 1
    _ok("표준 값 (tr_id/custtype/Content-Type)")

    if headers["User-Agent"] != settings.http_user_agent:
        _fail("User-Agent", "settings 값과 다름")
        return 1
    _ok("User-Agent (settings 일치)")

    # extra 헤더 병합
    headers2 = client._build_headers(
        tr_id="TTTC0802U",
        tr_cont="N",
        extra={"hashkey": "abcdef1234567890", "custom": "X"},
    )
    if headers2["tr_cont"] != "N":
        _fail("tr_cont 병합", headers2["tr_cont"])
        return 1
    if headers2.get("hashkey") != "abcdef1234567890":
        _fail("extra: hashkey", str(headers2.get("hashkey")))
        return 1
    if headers2.get("custom") != "X":
        _fail("extra: custom", str(headers2.get("custom")))
        return 1
    _ok("extra 헤더 병합 (tr_cont/hashkey/custom)")

    # ------------------------------------------------------------
    # 5. _mask_secret
    # ------------------------------------------------------------
    print("\n[5] _mask_secret")
    sample = {
        "Content-Type": "application/json",
        "tr_id": "FHKST01010100",
        "authorization": "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.payload.signature",
        "appkey": "PSED321z" + "x" * 32,
        "appsecret": "RR0sFMVB" + "y" * 172,
        "hashkey": "shortkey",  # 12자 이하
    }
    masked = KisClient._mask_secret(sample)

    # 비민감 헤더는 그대로
    if masked["Content-Type"] != "application/json":
        _fail("비민감 헤더 보존", masked["Content-Type"])
        return 1
    if masked["tr_id"] != "FHKST01010100":
        _fail("tr_id 보존", masked["tr_id"])
        return 1
    _ok("비민감 헤더 원본 보존")

    # 민감 헤더는 마스킹
    if "eyJ0" in masked["authorization"] and len(masked["authorization"]) > 30:
        # 일부 노출은 OK이지만, payload/signature가 그대로면 NG
        if "payload" in masked["authorization"] or "signature" in masked["authorization"]:
            _fail("authorization 마스킹", "본문 노출")
            return 1
    if "payload" in masked["authorization"]:
        _fail("authorization", "마스킹 안 됨")
        return 1
    _ok("authorization 마스킹")

    if "x" * 10 in masked["appkey"]:
        _fail("appkey 마스킹", "본문 노출")
        return 1
    _ok("appkey 마스킹")

    if "y" * 10 in masked["appsecret"]:
        _fail("appsecret 마스킹", "본문 노출")
        return 1
    _ok("appsecret 마스킹")

    # 12자 이하는 "***"
    if masked["hashkey"] != "***":
        _fail("hashkey (12자 이하) 마스킹", masked["hashkey"])
        return 1
    _ok("짧은 값(<=12자) → ***")

    # 원본 불변
    if sample["authorization"] != "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.payload.signature":
        _fail("원본 불변", "_mask_secret이 입력을 변경함")
        return 1
    _ok("원본 dict 불변")

    # ------------------------------------------------------------
    # 6. 미구현 메서드는 NotImplementedError
    # ------------------------------------------------------------
    print("\n[6] 미구현 메서드 placeholder")
    not_yet = [
        ("_enforce_rate_limit", lambda: client._enforce_rate_limit()),
        ("request_get",          lambda: client.request_get("/x", "FHKST01010100")),
        ("request_post",         lambda: client.request_post("/x", "TTTC0802U", {})),
    ]
    for name, fn in not_yet:
        try:
            fn()
            _fail(f"{name} placeholder", "NotImplementedError 안 발생")
            return 1
        except NotImplementedError:
            _ok(f"{name}", "NotImplementedError")
        except Exception as e:
            _fail(f"{name}", f"다른 예외: {type(e).__name__}: {e}")
            return 1

    # 정리
    client.close()

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step 2-3 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-4 (레이트리밋 _enforce_rate_limit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())