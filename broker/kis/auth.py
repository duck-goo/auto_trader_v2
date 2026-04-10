"""
KIS 인증 모듈.

토큰 발급/캐싱/자동 갱신을 담당한다.

캐시 전략:
    1. 메모리 캐시 (최우선)
    2. 파일 캐시 (storage/tokens/kis_{mode}.json)
    3. KIS API 발급 (마지막 수단)

만료 판정:
    실제 만료 시각 - expiry_buffer_minutes 를 "만료"로 간주.
    토큰 만료 직전 타이밍 이슈 방지.

사용 예:
    from config.loader import load_settings
    from broker.kis.auth import KisAuth

    settings = load_settings()
    auth = KisAuth(settings)
    token = auth.get_access_token()  # 유효한 토큰 반환
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests

from broker.base import BrokerInterface
from broker.kis.endpoints import PATH_TOKEN_ISSUE
from config.loader import Settings
from logger import get_logger


KST = pytz.timezone("Asia/Seoul")


class KisAuthError(Exception):
    """KIS 인증 실패."""


@dataclass
class TokenInfo:
    """토큰 정보."""

    access_token: str
    token_type: str
    issued_at: datetime  # KST
    expires_at: datetime  # KST
    mode: str  # "mock" or "real"

    def to_dict(self) -> dict:
        """파일 저장용 dict 변환."""
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TokenInfo:
        """파일에서 읽은 dict → TokenInfo."""
        return cls(
            access_token=data["access_token"],
            token_type=data["token_type"],
            issued_at=datetime.fromisoformat(data["issued_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            mode=data["mode"],
        )


class KisAuth(BrokerInterface):
    """
    KIS 토큰 관리.

    인스턴스 1개를 만들어서 재사용하는 것이 원칙.
    여러 인스턴스를 만들면 캐시가 공유되지 않아 중복 발급 위험.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._memory_cache: TokenInfo | None = None
        self._cache_file = (
            settings.token_cache_dir / f"kis_{settings.mode}.json"
        )
        self._log_login = get_logger("login")
        self._log_error = get_logger("error")

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def get_access_token(self) -> str:
        """
        유효한 access token 반환.

        순서:
            1. 메모리 캐시 확인
            2. 파일 캐시 확인
            3. KIS API 신규 발급

        Returns:
            access_token 문자열

        Raises:
            KisAuthError: 모든 경로에서 실패
        """
        # 1. 메모리 캐시
        if self._memory_cache and self._is_valid(self._memory_cache):
            return self._memory_cache.access_token

        # 2. 파일 캐시
        file_token = self._load_from_file()
        if file_token and self._is_valid(file_token):
            self._memory_cache = file_token
            self._log_login.info(
                f"파일 캐시에서 토큰 로드 "
                f"(만료: {file_token.expires_at.isoformat()})"
            )
            return file_token.access_token

        # 3. 신규 발급
        self._log_login.info("토큰 신규 발급 시도")
        new_token = self._issue_new_token()
        self._memory_cache = new_token
        self._save_to_file(new_token)
        self._log_login.info(
            f"토큰 발급 성공 "
            f"(만료: {new_token.expires_at.isoformat()})"
        )
        return new_token.access_token

    def force_refresh(self) -> str:
        """
        캐시 무시하고 강제 재발급.

        주의: 빈번 호출 시 KIS 정책(1분 1회) 위반 가능.
        운영 중에는 사용 금지. 디버깅/복구 용도만.
        """
        self._log_login.warning("토큰 강제 재발급 요청")
        self._memory_cache = None
        new_token = self._issue_new_token()
        self._memory_cache = new_token
        self._save_to_file(new_token)
        return new_token.access_token

    # ------------------------------------------------------------
    # 내부 로직
    # ------------------------------------------------------------

    def _is_valid(self, token: TokenInfo) -> bool:
        """
        토큰이 유효한지 확인.

        - 모드 일치
        - 만료 시각 - 버퍼 > 현재시각
        """
        if token.mode != self._settings.mode:
            return False
        buffer = timedelta(
            minutes=self._settings.token_expiry_buffer_minutes
        )
        now = datetime.now(KST)
        return now < (token.expires_at - buffer)

    def _load_from_file(self) -> TokenInfo | None:
        """
        파일 캐시 로드.

        실패 시 None 반환 (예외 전파 금지).
        파일이 없거나 깨져있으면 그냥 새로 발급받으면 되므로.
        """
        if not self._cache_file.exists():
            return None
        try:
            with self._cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return TokenInfo.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self._log_error.warning(
                f"토큰 캐시 파일 손상, 무시하고 재발급: {e}"
            )
            return None

    def _save_to_file(self, token: TokenInfo) -> None:
        """
        파일 캐시 저장.

        원자적 쓰기: .tmp 파일에 쓴 후 rename.
        쓰기 중 프로그램 종료되어도 기존 파일은 무사.
        """
        tmp_file = self._cache_file.with_suffix(".json.tmp")
        try:
            with tmp_file.open("w", encoding="utf-8") as f:
                json.dump(token.to_dict(), f, ensure_ascii=False, indent=2)
            # Windows에서 대상 파일이 있으면 rename 실패 → replace 사용
            os.replace(tmp_file, self._cache_file)
        except OSError as e:
            self._log_error.error(f"토큰 캐시 저장 실패: {e}")
            # 저장 실패해도 메모리 캐시는 유효하므로 계속 진행
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    def _issue_new_token(self) -> TokenInfo:
        """
        KIS API 호출로 신규 토큰 발급.

        재시도 포함. 모든 시도 실패 시 KisAuthError.
        """
        url = f"{self._settings.kis_rest_url}{PATH_TOKEN_ISSUE}"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
        }
        headers = {"content-type": "application/json"}

        last_error: Exception | None = None
        for attempt in range(1, self._settings.token_retry_count + 1):
            try:
                self._log_login.info(
                    f"토큰 발급 요청 (시도 {attempt}/"
                    f"{self._settings.token_retry_count})"
                )
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._settings.request_timeout,
                )
                return self._parse_token_response(response)
            except (requests.RequestException, KisAuthError) as e:
                last_error = e
                self._log_error.warning(
                    f"토큰 발급 실패 (시도 {attempt}): {e}"
                )
                if attempt < self._settings.token_retry_count:
                    delay = self._settings.token_retry_delay * attempt
                    self._log_login.info(f"{delay}초 후 재시도")
                    time.sleep(delay)

        raise KisAuthError(
            f"토큰 발급 최종 실패 (시도 {self._settings.token_retry_count}회): "
            f"{last_error}"
        )

    def _parse_token_response(
        self, response: requests.Response
    ) -> TokenInfo:
        """
        KIS 응답 파싱.

        정상 응답 예:
            {
                "access_token": "eyJ...",
                "access_token_token_expired": "2026-04-11 09:30:00",
                "token_type": "Bearer",
                "expires_in": 86400
            }

        Raises:
            KisAuthError: HTTP 오류 또는 필수 필드 누락
        """
        if response.status_code != 200:
            # 에러 응답 본문도 함께 기록 (디버깅용)
            try:
                error_body = response.json()
            except ValueError:
                error_body = response.text
            raise KisAuthError(
                f"HTTP {response.status_code}: {error_body}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise KisAuthError(f"응답 JSON 파싱 실패: {e}") from e

        access_token = data.get("access_token")
        if not access_token:
            raise KisAuthError(
                f"응답에 access_token이 없음: {data}"
            )

        token_type = data.get("token_type", "Bearer")

        # 만료 시각 파싱
        # KIS는 "access_token_token_expired" 필드를 KST 문자열로 제공
        expired_str = data.get("access_token_token_expired")
        if expired_str:
            try:
                # "2026-04-11 09:30:00" 형식
                naive = datetime.strptime(expired_str, "%Y-%m-%d %H:%M:%S")
                expires_at = KST.localize(naive)
            except ValueError:
                # 포맷이 예상과 다르면 expires_in 사용
                expires_at = self._expires_at_from_seconds(
                    data.get("expires_in")
                )
        else:
            expires_at = self._expires_at_from_seconds(
                data.get("expires_in")
            )

        return TokenInfo(
            access_token=access_token,
            token_type=token_type,
            issued_at=datetime.now(KST),
            expires_at=expires_at,
            mode=self._settings.mode,
        )

    def _expires_at_from_seconds(self, expires_in: object) -> datetime:
        """
        expires_in(초)을 KST 만료 시각으로 변환.

        필드가 없거나 이상하면 기본 23시간 적용 (보수적).
        """
        try:
            seconds = int(expires_in) if expires_in is not None else 82800
        except (TypeError, ValueError):
            seconds = 82800  # 23시간
        return datetime.now(KST) + timedelta(seconds=seconds)