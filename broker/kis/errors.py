"""
KIS 브로커 예외 계층.

상위:
    KisError
        ├── KisAuthError    인증/토큰 관련
        ├── KisApiError     KIS 서버가 명시적으로 실패 응답 (rt_cd != "0", HTTP 4xx/5xx)
        ├── KisParseError   응답 형식이 예상과 다름 (필수 필드 누락 등)
        └── KisRateLimitError  레이트리밋 위반 감지

설계 원칙:
    - 호출자가 except KisError 한 줄로 모든 KIS 에러를 잡을 수 있게 한다.
    - KisApiError는 KIS가 알려준 코드/메시지를 그대로 보존한다 (디버깅용).
    - 네트워크 에러(requests.RequestException)는 감싸지 않는다.
      → 호출 계층에서 의도적으로 분리해서 잡아야 재시도 정책을 세울 수 있음.
"""

from __future__ import annotations


class KisError(Exception):
    """KIS 브로커 관련 모든 예외의 최상위 클래스."""


class KisAuthError(KisError):
    """토큰 발급/갱신 실패."""


class KisApiError(KisError):
    """
    KIS API가 명시적으로 실패 응답을 돌려줌.

    Attributes:
        rt_cd: KIS 응답의 rt_cd (성공="0")
        msg_cd: KIS 메시지 코드 (예: "EGW00123")
        msg: KIS 메시지 본문
        http_status: HTTP 상태 코드 (있으면)
        tr_id: 호출한 TR ID (있으면, 디버깅용)
    """

    def __init__(
        self,
        message: str,
        *,
        rt_cd: str | None = None,
        msg_cd: str | None = None,
        msg: str | None = None,
        http_status: int | None = None,
        tr_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.rt_cd = rt_cd
        self.msg_cd = msg_cd
        self.msg = msg
        self.http_status = http_status
        self.tr_id = tr_id

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.tr_id:
            parts.append(f"tr_id={self.tr_id}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        if self.rt_cd is not None:
            parts.append(f"rt_cd={self.rt_cd}")
        if self.msg_cd:
            parts.append(f"msg_cd={self.msg_cd}")
        if self.msg:
            parts.append(f"msg={self.msg}")
        return " | ".join(parts)


class KisParseError(KisError):
    """KIS 응답을 모델로 변환하다가 형식 불일치 발견."""


class KisRateLimitError(KisError):
    """레이트리밋 위반 또는 KIS가 'too many requests' 응답."""


# ============================================================
# 토큰 만료 메시지 코드 (msg_cd 기반 자동 재발급용 - 보조 안전망)
# ============================================================
#
# [공식 샘플 분석 결과 - examples_user/kis_auth.py]
#
# KIS 공식 샘플은 응답 본문의 msg_cd로 토큰 만료를 감지하는 패턴을
# 사용하지 않는다. 대신 시간 기반 만료 체크 한 가지 방법만 사용한다:
#
#   1) 발급 응답의 access_token_token_expired (KST 문자열) 저장
#   2) 사용 직전에 (현재시각 < 만료시각) 비교
#   3) 만료되었으면 재발급, 6시간 이내 재요청 시 KIS가 동일 토큰 반환
#
# 우리 KisAuth._is_valid()도 이미 시간 기반 만료 체크를 사용하므로
# (expires_at - buffer > now) 이것이 정답 경로다.
#
# 따라서 이 frozenset은 비워둔다. client.py(Step 2)는 매 호출 직전
# get_access_token()을 호출해서 KisAuth가 알아서 만료 판단/재발급
# 하도록 한다. msg_cd 기반 후크는 별도로 두지 않는다.
#
# 향후 운영 중 특정 msg_cd로 만료가 실측되면 보조 안전망으로 여기에
# 추가하고, client.py에서 1회 강제 재발급 후 재시도하는 로직을 켠다.
TOKEN_EXPIRED_MSG_CODES: frozenset[str] = frozenset()