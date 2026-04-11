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
from datetime import datetime
import pandas as pd

from broker.kis.endpoints import (
    PATH_INQUIRE_PRICE, TR_ID_INQUIRE_PRICE,
    PATH_INQUIRE_DAILY, TR_ID_INQUIRE_DAILY,
    PATH_INQUIRE_MINUTE, TR_ID_INQUIRE_MINUTE,
)
from broker.kis.parsers import (
    parse_price_snapshot,
    parse_daily_candles,
    parse_minute_candles,
)


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
    
    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> "pd.DataFrame":
        """
        일봉 조회.

        Args:
            code: 6자리 종목코드
            count: 개수 (1~100). KIS 1회 호출 한도.
            end_date: 기준일 (YYYYMMDD). None이면 오늘.

        Returns:
            DataFrame: datetime | open | high | low | close | volume
                      (과거 → 현재 오름차순)

        Raises:
            ValueError: 입력값 오류
        """
        _validate_code(code)
        if not (1 <= count <= 100):
            raise ValueError(f"count는 1~100: {count}")

        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        elif not (len(end_date) == 8 and end_date.isdigit()):
            raise ValueError(f"end_date 형식 YYYYMMDD: {end_date!r}")

        # 시작일 계산: count개 받기 위해 넉넉히 과거로 (주말/휴장 고려 2배)
        from datetime import timedelta
        start_dt = datetime.strptime(end_date, "%Y%m%d") - timedelta(
            days=count * 2 + 10
        )
        start_date = start_dt.strftime("%Y%m%d")

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",  # D=일, W=주, M=월
            "FID_ORG_ADJ_PRC": "0",  # 0=수정주가, 1=원주가
        }

        self._log.debug(f"일봉 조회: {code} {start_date}~{end_date}")
        response = self._client.request_get(
            path=PATH_INQUIRE_DAILY,
            tr_id=TR_ID_INQUIRE_DAILY,
            params=params,
        )
        df = parse_daily_candles(response)

        # count개 초과분 제거 (최근 기준)
        if len(df) > count:
            df = df.tail(count).reset_index(drop=True)

        self._log.info(
            f"일봉 조회: {code} {len(df)}개"
            + (f" ({df.iloc[0]['datetime'].date()}~{df.iloc[-1]['datetime'].date()})"
               if len(df) > 0 else "")
        )
        return df

    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> "pd.DataFrame":
        """
        분봉 조회 (1분봉).

        KIS는 1분봉만 직접 지원한다. 3/5/10분봉 등은 호출자가
        1분봉을 리샘플링해서 만들어야 한다.

        1회 호출로 최근 30개 반환 (KIS 고정).

        Args:
            code: 6자리 종목코드
            interval: "1"만 허용. 다른 값은 ValueError.

        Returns:
            DataFrame (최근 30개, 과거 → 현재)
        """
        _validate_code(code)
        if interval != "1":
            raise ValueError(
                f"분봉 interval은 '1'만 지원. 다른 봉은 리샘플링 필요: "
                f"{interval!r}"
            )

        # 현재 시각 기준 조회
        now_hms = datetime.now().strftime("%H%M%S")

        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": now_hms,
            "FID_PW_DATA_INCU_YN": "N",
        }

        self._log.debug(f"분봉 조회: {code} 기준시각={now_hms}")
        response = self._client.request_get(
            path=PATH_INQUIRE_MINUTE,
            tr_id=TR_ID_INQUIRE_MINUTE,
            params=params,
        )
        df = parse_minute_candles(response)
        self._log.info(f"분봉 조회: {code} {len(df)}개")
        return df