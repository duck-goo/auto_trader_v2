"""
KIS API 응답 → 도메인 모델 변환 파서.

설계 원칙:
    - 네트워크/파일 IO 절대 없음 (단위 테스트 용이)
    - 입력은 KisResponse, 출력은 dataclass 또는 DataFrame
    - 필수 필드 누락 시 KisParseError
    - KIS 약어를 도메인 이름으로 매핑 (stck_prpr → price)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytz

from broker.kis.errors import KisParseError
from broker.kis.models import KisResponse, PriceSnapshot


KST = pytz.timezone("Asia/Seoul")


def _to_int(value: Any, field: str) -> int:
    """
    KIS 응답 값을 int로 변환.

    KIS는 숫자도 문자열로 보냄 ("70000"). 빈 문자열/None은 0 처리.
    음수도 허용 (전일대비 등).
    """
    if value is None or value == "":
        return 0
    try:
        # "1,234" 같은 콤마 방어
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))  # "70000.00" 같은 경우 대비
    except (ValueError, TypeError) as e:
        raise KisParseError(
            f"int 변환 실패: field={field}, value={value!r}"
        ) from e


def _to_float(value: Any, field: str) -> float:
    """KIS 응답 값을 float로 변환."""
    if value is None or value == "":
        return 0.0
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except (ValueError, TypeError) as e:
        raise KisParseError(
            f"float 변환 실패: field={field}, value={value!r}"
        ) from e


def parse_price_snapshot(
    response: KisResponse,
    code: str,
) -> PriceSnapshot:
    """
    inquire-price 응답을 PriceSnapshot으로 변환.

    Args:
        response: KisClient.request_get() 결과
        code: 조회한 종목코드 (응답에 종목코드가 없으므로 호출자가 전달)

    Returns:
        PriceSnapshot

    Raises:
        KisParseError: output이 dict가 아니거나 필수 필드 누락
    """
    output = response.output
    if not isinstance(output, dict):
        raise KisParseError(
            f"inquire-price output이 dict가 아님: {type(output).__name__}"
        )
    if not output:
        raise KisParseError(
            f"inquire-price output이 비어있음 (code={code}). "
            f"종목코드가 잘못되었거나 휴장일 가능성."
        )

    return PriceSnapshot(
        code=code,
        name="",  # KIS inquire-price 응답에는 종목명 없음 (TODO: 별도 TR)
        price=_to_int(output.get("stck_prpr"), "stck_prpr"),
        open=_to_int(output.get("stck_oprc"), "stck_oprc"),
        high=_to_int(output.get("stck_hgpr"), "stck_hgpr"),
        low=_to_int(output.get("stck_lwpr"), "stck_lwpr"),
        prev_close=_to_int(output.get("stck_sdpr"), "stck_sdpr"),
        change=_to_int(output.get("prdy_vrss"), "prdy_vrss"),
        change_rate=_to_float(output.get("prdy_ctrt"), "prdy_ctrt"),
        volume=_to_int(output.get("acml_vol"), "acml_vol"),
        timestamp=datetime.now(KST),
    )