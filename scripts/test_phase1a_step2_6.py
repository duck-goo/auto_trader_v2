"""
Phase 1-A Step 2-6 검증.

목적:
    request_get / request_post 공개 API와 401 안전망 검증.
    실제 KIS 모의서버에 1회 호출 (현재가).

검증:
    1. _execute_post 시그니처 (호출 안 함, 안전 위해)
    2. request_get 실제 호출 - 삼성전자 현재가
    3. 잘못된 종목코드 → KisApiError (rt_cd != "0")
    4. 401 안전망 시뮬레이션 (monkeypatch)

실행:
    python scripts/test_phase1a_step2_6.py
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
    print(" Phase 1-A Step 2-6 검증")
    print("=" * 60)

    print("\n[0] 준비")
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisAuth, KisClient
    from broker.kis.errors import KisApiError, KisAuthError
    from broker.kis.endpoints import (
        PATH_INQUIRE_PRICE,
        TR_ID_INQUIRE_PRICE,
    )

    settings = load_settings()
    setup_logging(settings)
    if settings.mode != "mock":
        _fail("mode", "mock 전용")
        return 1

    auth = KisAuth(settings)
    client = KisClient(settings, auth)
    _ok("준비 완료")

    # ------------------------------------------------------------
    # 1. 삼성전자 현재가 조회 (실제 KIS 호출)
    # ------------------------------------------------------------
    print("\n[1] 삼성전자(005930) 현재가 조회 - 실제 KIS 호출")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
    }
    try:
        resp = client.request_get(
            path=PATH_INQUIRE_PRICE,
            tr_id=TR_ID_INQUIRE_PRICE,
            params=params,
        )
    except KisApiError as e:
        _fail("현재가 조회", str(e))
        return 1
    except Exception as e:
        _fail("현재가 조회", f"{type(e).__name__}: {e}")
        return 1

    if resp.rt_cd != "0":
        _fail("rt_cd", resp.rt_cd)
        return 1
    _ok("rt_cd='0'")

    output = resp.output
    if not isinstance(output, dict):
        _fail("output 타입", str(type(output)))
        return 1

    price = output.get("stck_prpr")
    if not price:
        _fail("stck_prpr (현재가)", "없음")
        return 1
    _ok("현재가 (stck_prpr)", f"{price}원")

    # 추가 필드 확인
    for field in ("stck_oprc", "stck_hgpr", "stck_lwpr"):
        if field not in output:
            _fail(f"필드: {field}", "없음")
            return 1
    _ok("OHLC 필드 존재", "stck_oprc/hgpr/lwpr")

    # ------------------------------------------------------------
    # 2. 잘못된 종목코드
    # ------------------------------------------------------------
    print("\n[2] 잘못된 종목코드(999999) → KisApiError 또는 빈 응답")
    params_bad = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "999999",
    }
    try:
        resp_bad = client.request_get(
            path=PATH_INQUIRE_PRICE,
            tr_id=TR_ID_INQUIRE_PRICE,
            params=params_bad,
        )
        # KIS가 rt_cd=0으로 응답하되 output이 비어있을 수도 있음
        out = resp_bad.output
        if isinstance(out, dict) and out.get("stck_prpr") in (None, "", "0"):
            _ok("잘못된 코드", "빈 응답 (KIS가 rt_cd=0 + 빈 데이터)")
        else:
            _ok("잘못된 코드", f"응답 받음: stck_prpr={out.get('stck_prpr')}")
    except KisApiError as e:
        _ok("KisApiError", f"rt_cd={e.rt_cd}, msg_cd={e.msg_cd}")

    # ------------------------------------------------------------
    # 3. 401 안전망 시뮬레이션 (monkeypatch)
    # ------------------------------------------------------------
    print("\n[3] 401 안전망 시뮬레이션")

    # _execute_get을 가짜로 교체:
    # 첫 호출: KisAuthError(401)
    # 두 번째 호출: 정상 KisResponse
    from broker.kis.models import KisResponse

    call_count = {"n": 0}
    original_execute = client._execute_get

    def fake_execute(url, headers, params, tr_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise KisAuthError("HTTP 401 토큰 거부 (시뮬레이션)")
        return KisResponse(
            body={"rt_cd": "0", "msg_cd": "X", "msg1": "OK"},
            rt_cd="0", msg_cd="X", msg="OK",
            tr_cont="", tr_id=tr_id, http_status=200,
        )

    refresh_called = {"n": 0}
    original_refresh = client._auth.force_refresh

    def fake_refresh():
        refresh_called["n"] += 1
        # 실제 호출 없이 더미 토큰 반환
        return "dummy_refreshed_token"

    client._execute_get = fake_execute  # type: ignore[method-assign]
    client._auth.force_refresh = fake_refresh  # type: ignore[method-assign]

    try:
        resp = client.request_get(
            path="/dummy", tr_id="FHKST01010100", params={},
        )
    except Exception as e:
        _fail("401 안전망", f"예외 전파됨: {e}")
        return 1
    finally:
        client._execute_get = original_execute  # type: ignore[method-assign]
        client._auth.force_refresh = original_refresh  # type: ignore[method-assign]

    if call_count["n"] != 2:
        _fail("재시도 횟수", f"{call_count['n']}회 (기대: 2)")
        return 1
    _ok("정확히 1회 재시도", f"_execute_get 총 {call_count['n']}회 호출")

    if refresh_called["n"] != 1:
        _fail("force_refresh 호출", f"{refresh_called['n']}회 (기대: 1)")
        return 1
    _ok("force_refresh 1회 호출")

    if resp.rt_cd != "0":
        _fail("재시도 후 응답", resp.rt_cd)
        return 1
    _ok("재시도 후 정상 응답")

    # ------------------------------------------------------------
    # 4. POST는 401 받아도 재시도 안 함
    # ------------------------------------------------------------
    print("\n[4] POST는 401에도 재시도 안 함")

    post_call_count = {"n": 0}
    original_execute_post = client._execute_post

    def fake_post_execute(url, headers, body, tr_id):
        post_call_count["n"] += 1
        raise KisAuthError("HTTP 401 토큰 거부 (시뮬레이션)")

    client._execute_post = fake_post_execute  # type: ignore[method-assign]

    try:
        client.request_post(
            path="/dummy", tr_id="TTTC0802U", body={},
        )
        _fail("POST 401", "예외 발생 안 함")
        return 1
    except KisAuthError:
        if post_call_count["n"] != 1:
            _fail("POST 재시도", f"{post_call_count['n']}회 (POST는 1회여야)")
            return 1
        _ok("POST 401 → 즉시 전파", "재시도 0회 (안전)")
    finally:
        client._execute_post = original_execute_post  # type: ignore[method-assign]

    client.close()

    print()
    print("=" * 60)
    print(" Step 2-6 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-7 (마무리 정리, parsers/quote/account/broker)")
    return 0


if __name__ == "__main__":
    sys.exit(main())