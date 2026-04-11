"""
KIS REST API 통신 클라이언트.

이 클라이언트는 KIS API와 통신하는 유일한 통로다.
quote/account/order 모듈은 모두 이 클라이언트를 거쳐서 호출한다.

책임:
    - 토큰 관리 (KisAuth 위임)
    - 헤더 표준 조립 (Authorization, appkey, appsecret, tr_id, custtype 등)
    - TR_ID 자동 변환 (모의투자: 첫글자 T/J/C → V)
    - 레이트리밋 (Step 2-4)
    - HTTP 호출 (Step 2-5, Step 2-6)
    - 응답 검증 (rt_cd, HTTP 상태) (Step 2-5)
    - 네트워크 예외 재시도 (GET 한정, POST는 재시도 없음) (Step 2-5/2-6)
    - 로깅 (민감정보 마스킹)

책임이 아닌 것:
    - 응답 본문을 도메인 모델로 변환 → parsers.py
    - 종목코드 검증 등 도메인 규칙 → 호출자
    - 토큰 발급/캐싱 → KisAuth

설계 원칙:
    - POST는 절대 자동 재시도 안 함 (자동매매 안전성)
    - HTTP 401은 GET에 한해 강제 재발급 후 1회 재시도 (Step 2-5)
    - 단일 인스턴스 사용 권장 (레이트리밋 락이 인스턴스 단위)

구현 단계:
    Step 2-3 (현재): 골격 + TR_ID 변환 + 헤더 조립 + 마스킹
    Step 2-4: 레이트리밋
    Step 2-5: HTTP 검증 + 실행 메서드
    Step 2-6: request_get / request_post 공개 API
    Step 2-7: 401 자동 재시도 안전망
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests

from broker.kis.auth import KisAuth
from broker.kis.errors import (
    KisApiError,
    KisAuthError,
    RATE_LIMIT_MSG_CODES,
)
from broker.kis.models import KisResponse
from config.loader import Settings
from logger import get_logger

# ============================================================
# 헤더 마스킹 대상 (로그 출력 시 값을 가린다)
# ============================================================
_SENSITIVE_HEADER_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "appkey",
        "appsecret",
        "hashkey",
    }
)


class KisClient:
    """
    KIS REST API 통신 클라이언트.

    인스턴스는 1개만 만들어서 재사용하는 것이 원칙.
    여러 인스턴스를 만들면 레이트리밋 락이 공유되지 않아
    KIS 정책 위반 위험 (모의: 2 req/s).
    """

    def __init__(self, settings: Settings, auth: KisAuth) -> None:
        """
        Args:
            settings: load_settings() 결과
            auth: KisAuth 인스턴스 (토큰 관리)
        """
        self._settings = settings
        self._auth = auth

        # requests.Session: 연결 재사용으로 file descriptor 누수 방지.
        # python-kis 이슈 #58 사례 대응.
        self._session = requests.Session()

        # 레이트리밋 락 (Step 2-4에서 사용)
        self._rate_lock = threading.Lock()
        self._last_call_at: float = 0.0  # time.monotonic()

        # 로거
        self._log = get_logger("system")
        self._error_log = get_logger("error")

        self._log.info(
            f"KisClient 초기화: mode={settings.mode}, "
            f"rate_limit={settings.kis_rate_limit_interval}s"
        )

    # ============================================================
    # 라이프사이클
    # ============================================================

    def close(self) -> None:
        """
        Session 정리. 프로그램 종료 시 호출 권장.

        호출 후에는 이 클라이언트로 더 이상 요청을 보낼 수 없다.
        호출 안 해도 GC가 정리하지만, 명시적으로 닫는 것이 안전.
        """
        try:
            self._session.close()
            self._log.info("KisClient session 종료")
        except Exception as e:
            # 종료 단계의 예외는 삼킨다 (이미 끝나는 중)
            self._error_log.warning(f"KisClient close 중 예외 (무시): {e}")

    def __enter__(self) -> KisClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ============================================================
    # 공개 API (Step 2-6에서 구현)
    # ============================================================

    def request_get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        *,
        tr_cont: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> KisResponse:
        """
        GET 요청.
        - 네트워크/레이트리밋 에러는 재시도
        - 401은 강제 재발급 후 1회 재시도
        - 비즈니스 rt_cd 에러는 즉시 전파
        """
        resolved_tr_id = self._resolve_tr_id(tr_id)
        url = f"{self._settings.kis_rest_url}{path}"
        max_attempts = self._settings.request_retry_count

        refreshed_on_401 = False
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            headers = self._build_headers(resolved_tr_id, tr_cont, extra_headers)
            try:
                return self._execute_get(url, headers, params, resolved_tr_id)

            except KisAuthError as e:
                # 401 안전망: 정확히 1회만 강제 재발급
                if refreshed_on_401:
                    raise
                self._log.warning(f"401 감지, 토큰 강제 재발급: {e}")
                try:
                    self._auth.force_refresh()
                except Exception as refresh_err:
                    raise KisAuthError(
                        f"401 후 토큰 재발급 실패: {refresh_err}"
                    ) from refresh_err
                refreshed_on_401 = True
                continue  # 같은 attempt 번호로 즉시 재시도 (지연 없음)

            except KisApiError as e:
                # 레이트리밋은 재시도, 나머지는 즉시 전파
                is_rate_limit = (
                    (e.msg_cd and e.msg_cd in RATE_LIMIT_MSG_CODES)
                    or (e.msg and "EGW00201" in e.msg)
                    or (e.msg and "초당" in e.msg)
                )
                if not is_rate_limit:
                    raise
                last_error = e
                self._log.warning(
                    f"GET 레이트리밋 재시도 {attempt}/{max_attempts} "
                    f"tr_id={resolved_tr_id}"
                )
                if attempt < max_attempts:
                    delay = self._settings.request_retry_delay * attempt
                    time.sleep(delay)

        raise KisApiError(
            f"GET 레이트리밋 재시도 실패 ({max_attempts}회): {last_error}",
            tr_id=resolved_tr_id,
        )

    def request_post(
        self,
        path: str,
        tr_id: str,
        body: dict[str, Any],
        *,
        tr_cont: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> KisResponse:
        """
        POST 요청.

        - 절대 자동 재시도하지 않음 (자동매매 안전성)
        - 네트워크 예외도 그대로 전파 → 호출자가 미체결 조회로 검증해야 함
        - HTTP 401도 재시도 없이 그대로 전파
        - rt_cd != "0" 시 KisApiError
        """
        resolved_tr_id = self._resolve_tr_id(tr_id)
        url = f"{self._settings.kis_rest_url}{path}"
        headers = self._build_headers(resolved_tr_id, tr_cont, extra_headers)

        return self._execute_post(url, headers, body, resolved_tr_id)

    # ============================================================
    # 내부: 실제 HTTP 실행
    # ============================================================

    def _execute_get(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        tr_id: str,
    ) -> KisResponse:
        """GET 실행 + 네트워크/레이트리밋 재시도."""
        last_error: Exception | None = None
        max_attempts = self._settings.request_retry_count

        # 진입 로그는 INFO로 강제 (디버깅용)
        self._log.info(
            f"[_execute_get] 진입 tr_id={tr_id} max_attempts={max_attempts}"
        )

        for attempt in range(1, max_attempts + 1):
            self._log.info(f"[_execute_get] 시도 {attempt}/{max_attempts}")
            try:
                self._enforce_rate_limit()
                res = self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self._settings.request_timeout,
                )
                return self._validate_response(res, tr_id)

            except Exception as e:
                # 예외 타입 로깅
                self._log.warning(
                    f"[_execute_get] 예외 발생: "
                    f"type={type(e).__name__}, msg={str(e)[:200]}"
                )

                # 네트워크 예외는 재시도
                if isinstance(e, requests.RequestException):
                    last_error = e

                # KisApiError 중 레이트리밋만 재시도
                elif isinstance(e, KisApiError):
                    text = f"{e.msg_cd or ''} {e.msg or ''} {str(e)}"
                    if "EGW00201" in text or "초당" in text:
                        last_error = e
                        self._log.warning(
                            f"[_execute_get] 레이트리밋 재시도 대상"
                        )
                    else:
                        self._log.info(
                            f"[_execute_get] 비-레이트리밋 KisApiError → 전파"
                        )
                        raise
                else:
                    # KisAuthError 등 나머지는 전파
                    raise

            # 재시도 대기
            if attempt < max_attempts:
                delay = self._settings.request_retry_delay * attempt
                self._log.info(f"[_execute_get] {delay}초 후 재시도")
                time.sleep(delay)

        raise KisApiError(
            f"GET 실패 (최종 {max_attempts}회): {last_error}",
            tr_id=tr_id,
        )
    
    def _execute_post(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        tr_id: str,
    ) -> KisResponse:
        """
        POST 실행. 절대 재시도 없음.

        네트워크 예외도 그대로 전파한다. 호출자(주문 모듈)는 이 예외를
        받으면 반드시 미체결 조회로 실제 접수 여부를 확인한 후
        다음 액션을 결정해야 한다.
        """
        try:
            self._enforce_rate_limit()
            self._log.debug(
                f"POST {url} tr_id={tr_id} "
                f"body_keys={list(body.keys())} "
                f"headers={self._mask_secret(headers)}"
            )
            res = self._session.post(
                url,
                headers=headers,
                data=json.dumps(body),
                timeout=self._settings.request_timeout,
            )
        except requests.RequestException as e:
            raise KisApiError(
                f"POST 네트워크 실패 (재시도 없음 - 호출자가 미체결 확인 필요): {e}",
                tr_id=tr_id,
            ) from e
        return self._validate_response(res, tr_id)

    def _resolve_tr_id(self, tr_id: str) -> str:
        """
        모의투자 모드에서 실전용 TR_ID를 모의용으로 변환.

        규칙 (공식 examples_user/kis_auth.py 기준):
            첫 글자가 T/J/C 이고 모의투자(vps)이면 → 첫 글자를 V로 교체
            그 외에는 변환하지 않음

        F로 시작하는 시세 TR_ID(예: FHKST01010100)는 모의/실전 동일하므로
        변환하지 않는다.

        실전 모드(prod)에서는 입력값 그대로 반환한다.

        Args:
            tr_id: 호출자가 전달한 TR_ID (보통 실전 기준)

        Returns:
            실제 호출에 사용할 TR_ID

        Raises:
            ValueError: tr_id가 빈 문자열
        """
        if not tr_id:
            raise ValueError("tr_id가 비어있습니다.")

        if self._settings.mode != "mock":
            return tr_id

        if tr_id[0] in ("T", "J", "C"):
            return "V" + tr_id[1:]

        return tr_id

    # ============================================================
    # 내부: 헤더 조립
    # ============================================================

    def _build_headers(
        self,
        tr_id: str,
        tr_cont: str = "",
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        KIS 표준 헤더 조립.

        공식 샘플(examples_user/kis_auth.py)의 헤더 구성을 따른다:
            Content-Type, Accept, charset, User-Agent (기본)
            authorization, appkey, appsecret (인증)
            tr_id, tr_cont, custtype (TR)

        Args:
            tr_id: 이미 _resolve_tr_id로 변환된 TR_ID
            tr_cont: 페이징 토큰 ("" / "N" / "F" / "M")
            extra: 추가/덮어쓸 헤더 (예: hashkey, gt_uid)

        Returns:
            완성된 헤더 dict (호출 직전 사용)

        Raises:
            KisAuthError: 토큰 발급 실패
        """
        # 토큰 가져오기 (KisAuth가 시간 기반 만료 자동 처리)
        try:
            access_token = self._auth.get_access_token()
        except KisAuthError:
            # 그대로 전파 (호출자가 처리)
            raise
        except Exception as e:
            # 예상치 못한 예외도 KisAuthError로 통일
            raise KisAuthError(f"토큰 조회 실패: {e}") from e

        headers: dict[str, str] = {
            # 공식 기본 헤더
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
            "User-Agent": self._settings.http_user_agent,
            # 인증
            "authorization": f"Bearer {access_token}",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
            # TR
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",  # 개인고객 (제휴사는 "B")
        }

        if extra:
            for k, v in extra.items():
                headers[k] = str(v)

        return headers

    # ============================================================
    # 내부: 로그 마스킹
    # ============================================================

    @staticmethod
    def _mask_secret(headers: dict[str, str]) -> dict[str, str]:
        """
        헤더 dict에서 민감정보를 마스킹한 사본 반환.

        원본은 변경하지 않는다 (frozen 의도와 호환).

        마스킹 대상:
            authorization, appkey, appsecret, hashkey
            (대소문자 무관)

        마스킹 방식:
            12자 이하 → "***"
            그 외 → 앞 4 + "..." + 뒤 4
        """
        masked: dict[str, str] = {}
        for key, value in headers.items():
            if key.lower() in _SENSITIVE_HEADER_KEYS:
                masked[key] = KisClient._mask_value(value)
            else:
                masked[key] = value
        return masked

    @staticmethod
    def _mask_value(value: str) -> str:
        """단일 값 마스킹."""
        if not isinstance(value, str):
            return "***"
        if len(value) <= 12:
            return "***"
        return f"{value[:4]}...{value[-4:]}"

    # ============================================================
    # 내부: 레이트리밋 (Step 2-4에서 구현)
    # ============================================================

    def _enforce_rate_limit(self) -> None:
        """
        모드별 최소 호출 간격을 보장한다.

        공식 샘플 _smartSleep 값:
            실전 (prod): 0.05초 (20 req/s)
            모의 (vps):  0.5초  (2 req/s)

        동작 방식:
            1. 락 획득
            2. 마지막 호출 이후 경과 시간 계산
            3. 최소 간격 미달이면 부족한 만큼만 sleep
            4. 호출 시각 갱신
            5. 락 해제

        락 안에서 sleep하는 이유:
            여러 스레드가 동시에 들어와도 한 줄로 줄세워야 함.
            락 밖에서 sleep하면 동시에 sleep 풀려서 동시 호출됨.

        interval이 0이면 레이트리밋 비활성화 (테스트/디버깅 용도).
        """
        interval = self._settings.kis_rate_limit_interval
        if interval <= 0:
            return

        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_call_at
            wait = interval - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_call_at = time.monotonic()

    # ============================================================
    # 내부: HTTP 응답 검증 (Step 2-5에서 구현)
    # ============================================================

    def _validate_response(
        self,
        res: requests.Response,
        tr_id: str,
    ) -> KisResponse:
        """
        HTTP 응답 검증 + KisResponse 생성.

        검증 순서:
            1. HTTP 401 → KisAuthError (호출자가 토큰 재발급 후 재시도 결정)
            2. HTTP 비-200 → KisApiError (http_status, msg=res.text)
            3. JSON 파싱 실패 → KisApiError
            4. rt_cd != "0" → KisApiError (rt_cd, msg_cd, msg 보존)
            5. 모두 통과 → KisResponse 반환

        Raises:
            KisAuthError: HTTP 401
            KisApiError: HTTP 비-200, JSON 파싱 실패, rt_cd 실패
        """
        # 1. HTTP 401 (토큰 거부) - 안전망용 별도 예외
        if res.status_code == 401:
            raise KisAuthError(
                f"HTTP 401 토큰 거부 (tr_id={tr_id})"
            )

        # 2. HTTP 비-200
        if res.status_code != 200:
            try:
                error_text = res.text[:500]
            except Exception:
                error_text = "<응답 본문 읽기 실패>"
            raise KisApiError(
                f"HTTP {res.status_code}",
                http_status=res.status_code,
                tr_id=tr_id,
                msg=error_text,
            )

        # 3. JSON 파싱
        try:
            body = res.json()
        except ValueError as e:
            try:
                snippet = res.text[:500]
            except Exception:
                snippet = "<응답 본문 읽기 실패>"
            raise KisApiError(
                f"응답 JSON 파싱 실패: {e}",
                http_status=res.status_code,
                tr_id=tr_id,
                msg=snippet,
            ) from e

        if not isinstance(body, dict):
            raise KisApiError(
                f"응답 본문이 dict가 아님: {type(body).__name__}",
                http_status=res.status_code,
                tr_id=tr_id,
            )

        # 4. rt_cd 검증
        rt_cd = str(body.get("rt_cd", ""))
        msg_cd = str(body.get("msg_cd", ""))
        msg = str(body.get("msg1", ""))

        if rt_cd != "0":
            raise KisApiError(
                "KIS 응답 실패",
                rt_cd=rt_cd,
                msg_cd=msg_cd,
                msg=msg,
                http_status=res.status_code,
                tr_id=tr_id,
            )

        # 5. 페이징 헤더 추출 (대소문자 무관, 없으면 빈 문자열)
        tr_cont = ""
        for k, v in res.headers.items():
            if k.lower() == "tr_cont":
                tr_cont = str(v)
                break

        return KisResponse(
            body=body,
            rt_cd=rt_cd,
            msg_cd=msg_cd,
            msg=msg,
            tr_cont=tr_cont,
            tr_id=tr_id,
            http_status=res.status_code,
        )