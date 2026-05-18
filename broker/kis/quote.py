"""
KIS 시세 조회 모듈.

KisClient를 사용해 시세를 조회하고 parsers로 도메인 모델로 변환한다.
얇은 wrapper. 비즈니스 로직은 호출자(전략)에 둔다.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

import pandas as pd
import pytz

from broker.kis.client import KisClient
from broker.kis.endpoints import (
    PATH_INQUIRE_DAILY,
    PATH_INQUIRE_MINUTE,
    PATH_INQUIRE_PRICE,
    TR_ID_INQUIRE_DAILY,
    TR_ID_INQUIRE_MINUTE,
    TR_ID_INQUIRE_PRICE,
)
from broker.kis.models import PriceSnapshot
from broker.kis.parsers import (
    parse_daily_candles,
    parse_minute_candles,
    parse_price_snapshot,
)
from logger import get_logger


_CODE_PATTERN = re.compile(r"^\d{6}$")
_TIME_PATTERN = re.compile(r"^\d{6}$")
_KST = pytz.timezone("Asia/Seoul")
_SAME_DAY_MINUTE_BACKFILL_WINDOW_DELAY_SECONDS = 0.7


def _validate_code(code: str) -> None:
    """종목코드 형식 검증: 6자리 숫자."""
    if not isinstance(code, str) or not _CODE_PATTERN.match(code):
        raise ValueError(
            f"종목코드는 6자리 숫자 문자열이어야 합니다: {code!r}"
        )


def _validate_hms(name: str, value: str) -> str:
    if not isinstance(value, str) or not _TIME_PATTERN.match(value):
        raise ValueError(f"{name} must be HHMMSS digits: {value!r}")
    return value


def _filter_candles_to_kst_date(
    df: "pd.DataFrame",
    target_date,
) -> "pd.DataFrame":
    if df.empty or "datetime" not in df.columns:
        return df
    same_day_mask = df["datetime"].map(
        lambda value: value.astimezone(_KST).date() == target_date
    )
    return df.loc[same_day_mask].reset_index(drop=True)


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
        """
        _validate_code(code)

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
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
        """
        _validate_code(code)
        if not (1 <= count <= 100):
            raise ValueError(f"count는 1~100: {count}")

        if end_date is None:
            end_date = datetime.now(_KST).strftime("%Y%m%d")
        elif not (len(end_date) == 8 and end_date.isdigit()):
            raise ValueError(f"end_date 형식 YYYYMMDD: {end_date!r}")

        start_dt = datetime.strptime(end_date, "%Y%m%d") - timedelta(
            days=count * 2 + 10
        )
        start_date = start_dt.strftime("%Y%m%d")

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }

        self._log.debug(f"일봉 조회: {code} {start_date}~{end_date}")
        response = self._client.request_get(
            path=PATH_INQUIRE_DAILY,
            tr_id=TR_ID_INQUIRE_DAILY,
            params=params,
        )
        df = parse_daily_candles(response)
        if len(df) > count:
            df = df.tail(count).reset_index(drop=True)

        self._log.info(
            f"일봉 조회: {code} {len(df)}개"
            + (
                f" ({df.iloc[0]['datetime'].date()}~"
                f"{df.iloc[-1]['datetime'].date()})"
                if len(df) > 0
                else ""
            )
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
        """
        _validate_code(code)
        if interval != "1":
            raise ValueError(
                f"분봉 interval은 '1'만 지원. 다른 봉은 리샘플링 필요: "
                f"{interval!r}"
            )

        now_hms = datetime.now(_KST).strftime("%H%M%S")
        df = self._fetch_minute_window(
            code=code,
            end_time=now_hms,
            include_past_data=False,
        )
        self._log.info(f"분봉 조회: {code} {len(df)}개")
        return df

    def get_same_day_minute_candles(
        self,
        code: str,
        *,
        end_time: str | None = None,
    ) -> "pd.DataFrame":
        """
        Backfill same-day 1-minute candles by moving the KIS time window.

        Important constraint from the official KIS sample:
        - stock minute data is same-day only
        - one request returns at most 30 rows
        """
        _validate_code(code)
        requested_end_time = (
            datetime.now(_KST).strftime("%H%M%S") if end_time is None else end_time
        )
        normalized_end_time = _validate_hms("end_time", requested_end_time)
        target_date = datetime.now(_KST).date()

        frames: list[pd.DataFrame] = []
        query_end_time = normalized_end_time
        seen_query_times: set[str] = set()
        market_open_time = "090000"
        max_windows = 20

        for _ in range(max_windows):
            if query_end_time in seen_query_times:
                raise RuntimeError(
                    "KIS minute backfill did not advance to an earlier window: "
                    f"code={code}, end_time={query_end_time}"
                )
            seen_query_times.add(query_end_time)

            window = self._fetch_minute_window(
                code=code,
                end_time=query_end_time,
                include_past_data=True,
            )
            window = _filter_candles_to_kst_date(window, target_date)
            if window.empty:
                break
            frames.append(window)

            oldest_dt = window.iloc[0]["datetime"].astimezone(_KST)
            oldest_hms = oldest_dt.strftime("%H%M%S")
            if oldest_hms <= market_open_time:
                break

            next_query_dt = oldest_dt - timedelta(seconds=1)
            if next_query_dt.strftime("%Y-%m-%d") != oldest_dt.strftime("%Y-%m-%d"):
                break
            if next_query_dt.strftime("%H%M%S") < market_open_time:
                break
            query_end_time = next_query_dt.strftime("%H%M%S")
            time.sleep(_SAME_DAY_MINUTE_BACKFILL_WINDOW_DELAY_SECONDS)
        else:
            raise RuntimeError(
                f"KIS minute backfill exceeded {max_windows} windows: code={code}"
            )

        if not frames:
            return pd.DataFrame(
                columns=[
                    "datetime",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "trade_value",
                ]
            )

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["datetime"],
            keep="last",
        )
        combined = combined.sort_values("datetime").reset_index(drop=True)
        self._log.info(
            f"당일 분봉 백필 조회: {code} {len(combined)}개 "
            f"(end_time={normalized_end_time})"
        )
        return combined

    def _fetch_minute_window(
        self,
        *,
        code: str,
        end_time: str,
        include_past_data: bool,
    ) -> "pd.DataFrame":
        normalized_end_time = _validate_hms("end_time", end_time)
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": normalized_end_time,
            "FID_PW_DATA_INCU_YN": "Y" if include_past_data else "N",
        }
        self._log.debug(
            f"분봉 조회: {code} 기준시각={normalized_end_time} "
            f"include_past_data={include_past_data}"
        )
        response = self._client.request_get(
            path=PATH_INQUIRE_MINUTE,
            tr_id=TR_ID_INQUIRE_MINUTE,
            params=params,
        )
        return parse_minute_candles(response)
