"""
Phase 1-A Step 2-5 검증.

목적:
    _validate_response가 다양한 응답 케이스를 정확히 분기하는지 확인.
    네트워크 호출 없음. 가짜 Response 객체 사용.

검증 케이스:
    1. 정상 응답 (rt_cd="0") → KisResponse 반환
    2. HTTP 401 → KisAuthError
    3. HTTP 500 → KisApiError(http_status=500)
    4. JSON 파싱 실패 → KisApiError
    5. body가 dict 아님 → KisApiError
    6. rt_cd="1" → KisApiError(rt_cd, msg_cd, msg)
    7. tr_cont 헤더 추출
    8. tr_cont 헤더 없음 → 빈 문자열

실행:
    python scripts/test_phase1a_step2_5.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


class FakeResponse:
    """requests.Response 흉내. _validate_response가 사용하는 속성만 구현."""

    def __init__(
        self,
        status_code: int,
        body: object = None,
        text: str = "",
        headers: dict | None = None,
        raise_on_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text
        self.headers = headers or {}
        self._raise_on_json = raise_on_json

    def json(self) -> object:
        if self._raise_on_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def main() -> int:
    print("=" * 60)
    print(" Phase 1-A Step 2-5 검증")
    print("=" * 60)

    print("\n[0] 준비")
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisAuth, KisClient
    from broker.kis.errors import KisApiError, KisAuthError

    settings = load_settings()
    setup_logging(settings)
    auth = KisAuth(settings)
    client = KisClient(settings, auth)
    _ok("준비 완료")

    # ------------------------------------------------------------
    # 1. 정상 응답
    # ------------------------------------------------------------
    print("\n[1] 정상 응답 (rt_cd='0')")
    body = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상 처리 되었습니다.",
        "output": {"stck_prpr": "70000"},
    }
    fake = FakeResponse(
        status_code=200, body=body,
        text=json.dumps(body),
        headers={"tr_cont": ""},
    )
    try:
        resp = client._validate_response(fake, "FHKST01010100")
    except Exception as e:
        _fail("정상 응답", f"예외 발생: {type(e).__name__}: {e}")
        return 1

    if resp.rt_cd != "0":
        _fail("rt_cd", resp.rt_cd)
        return 1
    if resp.msg_cd != "MCA00000":
        _fail("msg_cd", resp.msg_cd)
        return 1
    if resp.tr_id != "FHKST01010100":
        _fail("tr_id", resp.tr_id)
        return 1
    if resp.output.get("stck_prpr") != "70000":
        _fail("output", str(resp.output))
        return 1
    _ok("KisResponse 반환", "rt_cd/msg_cd/tr_id/output 정확")

    # ------------------------------------------------------------
    # 2. HTTP 401
    # ------------------------------------------------------------
    print("\n[2] HTTP 401 → KisAuthError")
    fake = FakeResponse(status_code=401, text="Unauthorized")
    try:
        client._validate_response(fake, "FHKST01010100")
        _fail("401", "예외 발생 안 함")
        return 1
    except KisAuthError as e:
        if "401" not in str(e):
            _fail("401 메시지", str(e))
            return 1
        _ok("KisAuthError 발생", str(e))
    except Exception as e:
        _fail("401", f"잘못된 예외 타입: {type(e).__name__}")
        return 1

    # ------------------------------------------------------------
    # 3. HTTP 500
    # ------------------------------------------------------------
    print("\n[3] HTTP 500 → KisApiError")
    fake = FakeResponse(
        status_code=500,
        text="Internal Server Error",
    )
    try:
        client._validate_response(fake, "FHKST01010100")
        _fail("500", "예외 발생 안 함")
        return 1
    except KisApiError as e:
        if e.http_status != 500:
            _fail("http_status", str(e.http_status))
            return 1
        if e.tr_id != "FHKST01010100":
            _fail("tr_id 보존", str(e.tr_id))
            return 1
        _ok("KisApiError(http_status=500)", f"tr_id={e.tr_id}")
    except Exception as e:
        _fail("500", f"잘못된 예외 타입: {type(e).__name__}")
        return 1

    # ------------------------------------------------------------
    # 4. JSON 파싱 실패
    # ------------------------------------------------------------
    print("\n[4] JSON 파싱 실패 → KisApiError")
    fake = FakeResponse(
        status_code=200,
        text="<html>error page</html>",
        raise_on_json=True,
    )
    try:
        client._validate_response(fake, "FHKST01010100")
        _fail("JSON 실패", "예외 발생 안 함")
        return 1
    except KisApiError as e:
        if "JSON" not in str(e) and "파싱" not in str(e):
            _fail("메시지", str(e))
            return 1
        _ok("KisApiError (JSON 파싱 실패)", "메시지 정상")
    except Exception as e:
        _fail("JSON 실패", f"잘못된 예외 타입: {type(e).__name__}")
        return 1

    # ------------------------------------------------------------
    # 5. body가 dict 아님
    # ------------------------------------------------------------
    print("\n[5] body가 list → KisApiError")
    fake = FakeResponse(
        status_code=200,
        body=["wrong", "type"],
        text='["wrong","type"]',
    )
    try:
        client._validate_response(fake, "FHKST01010100")
        _fail("non-dict body", "예외 발생 안 함")
        return 1
    except KisApiError as e:
        if "dict" not in str(e):
            _fail("메시지", str(e))
            return 1
        _ok("KisApiError (body 타입 오류)")
    except Exception as e:
        _fail("non-dict", f"잘못된 예외 타입: {type(e).__name__}")
        return 1

    # ------------------------------------------------------------
    # 6. rt_cd != "0"
    # ------------------------------------------------------------
    print("\n[6] rt_cd='1' → KisApiError (rt_cd/msg_cd/msg 보존)")
    body = {
        "rt_cd": "1",
        "msg_cd": "OPSP0002",
        "msg1": "조회 결과가 없습니다.",
    }
    fake = FakeResponse(
        status_code=200, body=body,
        text=json.dumps(body),
    )
    try:
        client._validate_response(fake, "FHKST01010100")
        _fail("rt_cd=1", "예외 발생 안 함")
        return 1
    except KisApiError as e:
        if e.rt_cd != "1":
            _fail("rt_cd 보존", str(e.rt_cd))
            return 1
        if e.msg_cd != "OPSP0002":
            _fail("msg_cd 보존", str(e.msg_cd))
            return 1
        if e.msg != "조회 결과가 없습니다.":
            _fail("msg 보존", str(e.msg))
            return 1
        _ok("rt_cd/msg_cd/msg 보존", f"{e.msg_cd}: {e.msg}")
    except Exception as e:
        _fail("rt_cd=1", f"잘못된 예외 타입: {type(e).__name__}")
        return 1

    # ------------------------------------------------------------
    # 7. tr_cont 헤더 추출
    # ------------------------------------------------------------
    print("\n[7] tr_cont 헤더 추출")
    body = {"rt_cd": "0", "msg_cd": "X", "msg1": "Y"}
    fake = FakeResponse(
        status_code=200, body=body, text=json.dumps(body),
        headers={"tr_cont": "M", "Content-Type": "application/json"},
    )
    resp = client._validate_response(fake, "VTTC8434R")
    if resp.tr_cont != "M":
        _fail("tr_cont", resp.tr_cont)
        return 1
    if not resp.has_more_pages:
        _fail("has_more_pages", "M인데 False")
        return 1
    _ok("tr_cont='M' + has_more_pages=True")

    # 대소문자 무관
    fake = FakeResponse(
        status_code=200, body=body, text=json.dumps(body),
        headers={"TR_CONT": "F"},  # 대문자
    )
    resp = client._validate_response(fake, "VTTC8434R")
    if resp.tr_cont != "F":
        _fail("tr_cont 대소문자", resp.tr_cont)
        return 1
    _ok("tr_cont 헤더 대소문자 무관")

    # ------------------------------------------------------------
    # 8. tr_cont 헤더 없음
    # ------------------------------------------------------------
    print("\n[8] tr_cont 헤더 없음 → 빈 문자열")
    fake = FakeResponse(
        status_code=200, body=body, text=json.dumps(body),
        headers={},
    )
    resp = client._validate_response(fake, "FHKST01010100")
    if resp.tr_cont != "":
        _fail("tr_cont 기본값", repr(resp.tr_cont))
        return 1
    if resp.has_more_pages:
        _fail("has_more_pages", "빈 문자열인데 True")
        return 1
    _ok("tr_cont='' + has_more_pages=False")

    client.close()

    print()
    print("=" * 60)
    print(" Step 2-5 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-6 (request_get / request_post 공개 API)")
    return 0


if __name__ == "__main__":
    sys.exit(main())