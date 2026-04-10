"""
KIS 시세 조회 모듈.

KisClient를 사용해 시세를 조회하고 parsers로 도메인 모델로 변환한다.
얇은 wrapper. 비즈니스 로직은 호출자(전략)에 둔다.
"""

from __future__ import annotations

import re

from broker.kis.client import KisClient
from broker.kis.endpoints import PATH_INQUIRE_PRICE, TR_ID_INQUIRE_PRICE
from broker.kis.models import PriceSnapshot
from broker.kis.parsers import parse_price_snapshot
from logger import get_logger


_CODE_PATTERN = re.compile(r"^\d{6}$")


def _validate_code(code: str) -> None:
    """종목코드 형식 검증: 6자리 숫자."""
    if not isinstance(code, str) or not _CODE_PATTERN.match(code):
        raise ValueError(
            f"종목코드는 6자리 숫자 문자열이어야 합니다: {code!r}"
        )


class Quote:
    """
    시세 조회.

    KisClient 인스턴스를 주입받아 사용한다 (1 client → N quote 인스턴스 가능).
    """

    def __init__(self, client: KisClient) -> None:
        self._client = client
        self._log = get_logger("scan")

    def get_current_price(self, code: str) -> PriceSnapshot:
        """
        주식 현재가 조회.

        Args:
            code: 6자리 종목코드 (예: "005930")

        Returns:
            PriceSnapshot

        Raises:
            ValueError: 종목코드 형식 오류
            KisApiError: KIS 응답 실패
            KisParseError: 응답 파싱 실패
        """
        _validate_code(code)

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",  # 주식
            "FID_INPUT_ISCD": code,
        }

        self._log.debug(f"현재가 조회 요청: {code}")
        response = self._client.request_get(
            path=PATH_INQUIRE_PRICE,
            tr_id=TR_ID_INQUIRE_PRICE,
            params=params,
        )

        snapshot = parse_price_snapshot(response, code)
        self._log.info(
            f"현재가 조회: {code} = {snapshot.price:,}원 "
            f"({snapshot.change:+,}, {snapshot.change_rate:+.2f}%)"
        )
        return snapshot