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
from broker.kis.models import Balance, Holding, KisResponse, PriceSnapshot


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

import pandas as pd


# ============================================================
# 캔들 DataFrame 스키마 (고정)
# ============================================================
CANDLE_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]


def _empty_candle_df() -> "pd.DataFrame":
    """스키마는 유지하되 0행인 DataFrame."""
    return pd.DataFrame(
        {
            "datetime": pd.Series(dtype="datetime64[ns, Asia/Seoul]"),
            "open": pd.Series(dtype="int64"),
            "high": pd.Series(dtype="int64"),
            "low": pd.Series(dtype="int64"),
            "close": pd.Series(dtype="int64"),
            "volume": pd.Series(dtype="int64"),
        }
    )


def parse_daily_candles(response: KisResponse) -> "pd.DataFrame":
    """
    inquire-daily-itemchartprice 응답을 DataFrame으로 변환.

    KIS 응답 필드 (output2 배열의 각 원소):
        stck_bsop_date: 영업일 (YYYYMMDD)
        stck_oprc:      시가
        stck_hgpr:      고가
        stck_lwpr:      저가
        stck_clpr:      종가
        acml_vol:       누적거래량

    반환 DataFrame:
        datetime (datetime64[KST]) | open | high | low | close | volume
        - 과거 → 현재 오름차순 정렬
        - KIS 원본은 최신순이므로 역순 변환
        - 빈 데이터면 스키마만 있는 빈 DataFrame
    """
    output2 = response.body.get("output2")
    if output2 is None:
        return _empty_candle_df()
    if not isinstance(output2, list):
        raise KisParseError(
            f"일봉 output2가 list가 아님: {type(output2).__name__}"
        )
    if len(output2) == 0:
        return _empty_candle_df()

    rows = []
    for item in output2:
        if not isinstance(item, dict):
            raise KisParseError(f"일봉 항목이 dict가 아님: {type(item)}")
        date_str = item.get("stck_bsop_date", "")
        if not date_str:
            # 빈 날짜는 skip (KIS가 빈 행을 섞어 보낼 때가 있음)
            continue
        try:
            dt = datetime.strptime(date_str, "%Y%m%d")
            dt = KST.localize(dt)
        except ValueError as e:
            raise KisParseError(
                f"일봉 날짜 파싱 실패: {date_str}"
            ) from e

        rows.append(
            {
                "datetime": dt,
                "open": _to_int(item.get("stck_oprc"), "stck_oprc"),
                "high": _to_int(item.get("stck_hgpr"), "stck_hgpr"),
                "low": _to_int(item.get("stck_lwpr"), "stck_lwpr"),
                "close": _to_int(item.get("stck_clpr"), "stck_clpr"),
                "volume": _to_int(item.get("acml_vol"), "acml_vol"),
            }
        )

    if not rows:
        return _empty_candle_df()

    df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    # 과거 → 현재 오름차순
    df = df.sort_values("datetime").reset_index(drop=True)
    # 타입 강제
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype("int64")
    return df


def parse_minute_candles(response: KisResponse) -> "pd.DataFrame":
    """
    inquire-time-itemchartprice 응답(1분봉)을 DataFrame으로 변환.

    KIS 응답 필드 (output2 배열의 각 원소):
        stck_bsop_date: 영업일 (YYYYMMDD)
        stck_cntg_hour: 체결시각 (HHMMSS)
        stck_oprc/hgpr/lwpr/prpr: OHLC (종가는 prpr)
        cntg_vol:       체결거래량
    """
    output2 = response.body.get("output2")
    if output2 is None:
        return _empty_candle_df()
    if not isinstance(output2, list):
        raise KisParseError(
            f"분봉 output2가 list가 아님: {type(output2).__name__}"
        )
    if len(output2) == 0:
        return _empty_candle_df()

    rows = []
    for item in output2:
        if not isinstance(item, dict):
            raise KisParseError(f"분봉 항목이 dict가 아님: {type(item)}")
        date_str = item.get("stck_bsop_date", "")
        time_str = item.get("stck_cntg_hour", "")
        if not date_str or not time_str:
            continue
        try:
            dt = datetime.strptime(
                f"{date_str}{time_str.zfill(6)}", "%Y%m%d%H%M%S"
            )
            dt = KST.localize(dt)
        except ValueError as e:
            raise KisParseError(
                f"분봉 시각 파싱 실패: {date_str} {time_str}"
            ) from e

        rows.append(
            {
                "datetime": dt,
                "open": _to_int(item.get("stck_oprc"), "stck_oprc"),
                "high": _to_int(item.get("stck_hgpr"), "stck_hgpr"),
                "low": _to_int(item.get("stck_lwpr"), "stck_lwpr"),
                "close": _to_int(item.get("stck_prpr"), "stck_prpr"),
                "volume": _to_int(item.get("cntg_vol"), "cntg_vol"),
            }
        )

    if not rows:
        return _empty_candle_df()

    df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    df = df.sort_values("datetime").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype("int64")
    return df

def _parse_holding(item: dict) -> Holding:
    """output1 항목 → Holding. 수량 0인 종목은 호출자가 필터링."""
    if not isinstance(item, dict):
        raise KisParseError(f"holding 항목이 dict 아님: {type(item)}")

    return Holding(
        code=str(item.get("pdno", "")),
        name=str(item.get("prdt_name", "")),
        quantity=_to_int(item.get("hldg_qty"), "hldg_qty"),
        available=_to_int(item.get("ord_psbl_qty"), "ord_psbl_qty"),
        avg_price=_to_float(item.get("pchs_avg_pric"), "pchs_avg_pric"),
        current_price=_to_int(item.get("prpr"), "prpr"),
        eval_amount=_to_int(item.get("evlu_amt"), "evlu_amt"),
        profit=_to_int(item.get("evlu_pfls_amt"), "evlu_pfls_amt"),
        profit_rate=_to_float(item.get("evlu_pfls_rt"), "evlu_pfls_rt"),
    )


def parse_balance(response: KisResponse) -> Balance:
    """
    inquire-balance 응답 → Balance.

    - output1: 보유 종목 배열 (수량>0인 것만 포함)
    - output2: 잔고 요약 (배열, [0] 사용)
    - tr_cont "M"/"F" 이면 has_more_pages=True (Phase 1-A는 1페이지만 처리)

    Raises:
        KisParseError: 응답 구조 불일치
    """
    body = response.body

    # output1: 보유 종목
    output1 = body.get("output1", [])
    if not isinstance(output1, list):
        raise KisParseError(
            f"잔고 output1이 list 아님: {type(output1).__name__}"
        )

    holdings: list[Holding] = []
    for item in output1:
        try:
            h = _parse_holding(item)
        except KisParseError:
            raise
        # 수량 0인 종목 필터 (KIS가 전일 매도 종목도 포함시키는 경우 있음)
        if h.quantity > 0:
            holdings.append(h)

    # output2: 잔고 요약
    output2 = body.get("output2", [])
    if not isinstance(output2, list):
        raise KisParseError(
            f"잔고 output2가 list 아님: {type(output2).__name__}"
        )

    if len(output2) == 0:
        # 잔고 요약이 없는 경우 (신규 계좌 등): 0으로 채움
        cash = 0
        available_cash = 0
        total_eval = 0
        total_profit = 0
    else:
        summary = output2[0]
        if not isinstance(summary, dict):
            raise KisParseError(
                f"output2[0]이 dict 아님: {type(summary).__name__}"
            )
        cash = _to_int(summary.get("dnca_tot_amt"), "dnca_tot_amt")
        available_cash = _to_int(
            summary.get("prvs_rcdl_excc_amt"), "prvs_rcdl_excc_amt"
        )
        total_eval = _to_int(summary.get("tot_evlu_amt"), "tot_evlu_amt")
        total_profit = _to_int(
            summary.get("evlu_pfls_smtl_amt"), "evlu_pfls_smtl_amt"
        )

    return Balance(
        cash=cash,
        available_cash=available_cash,
        total_eval=total_eval,
        total_profit=total_profit,
        holdings=tuple(holdings),
        has_more_pages=response.has_more_pages,
        timestamp=datetime.now(KST),
    )