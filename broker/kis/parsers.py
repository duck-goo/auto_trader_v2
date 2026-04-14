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
from broker.kis.models import Balance, Holding, KisResponse, PriceSnapshot, OrderInfo, OrderSide, OrderStatus, OrderType


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
CANDLE_COLUMNS = [
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_value",
]


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
            "trade_value": pd.Series(dtype="int64"),
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
                "trade_value": _to_int(
                    item.get("acml_tr_pbmn"),
                    "acml_tr_pbmn",
                ),
            }
        )

    if not rows:
        return _empty_candle_df()

    df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    # 과거 → 현재 오름차순
    df = df.sort_values("datetime").reset_index(drop=True)
    # 타입 강제
    return df.astype(
        {
            "open": "int64",
            "high": "int64",
            "low": "int64",
            "close": "int64",
            "volume": "int64",
            "trade_value": "int64",
        }
    )


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
                "trade_value": 0,
            }
        )

    if not rows:
        return _empty_candle_df()

    df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df.astype(
        {
            "open": "int64",
            "high": "int64",
            "low": "int64",
            "close": "int64",
            "volume": "int64",
            "trade_value": "int64",
        }
    )

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

# ============================================================
# Phase 1-B: 주문 파서
# ============================================================

def _to_order_side(sll_buy_dvsn_cd: str) -> OrderSide:
    """
    KIS 매수/매도 구분 코드 → OrderSide.

    KIS 코드:
        "01" = 매도 (SELL)
        "02" = 매수 (BUY)

    Raises:
        KisParseError: 알 수 없는 코드
    """
    if sll_buy_dvsn_cd == "02":
        return OrderSide.BUY
    if sll_buy_dvsn_cd == "01":
        return OrderSide.SELL
    raise KisParseError(
        f"알 수 없는 sll_buy_dvsn_cd: {sll_buy_dvsn_cd!r} "
        "(기대값: '01'=매도, '02'=매수)"
    )


def _to_order_type(ord_dvsn_cd: str) -> OrderType:
    """
    KIS 주문구분 코드 → OrderType.

    KIS 코드:
        "00" = 지정가 (LIMIT)
        "01" = 시장가 (MARKET)

    그 외 코드(최유리, 최우선 등)는 LIMIT으로 통일.
    운영 중 다른 코드가 나오면 로그로 확인 후 여기에 추가할 것.
    """
    if ord_dvsn_cd == "01":
        return OrderType.MARKET
    return OrderType.LIMIT  # "00" 및 기타 전부 LIMIT으로 취급


def _parse_order_datetime(
    ord_dt: str,
    ord_tmd: str,
) -> datetime:
    """
    KIS 주문일(YYYYMMDD) + 주문시각(HHMMSS) → KST datetime.

    ord_dt 또는 ord_tmd가 비어있으면 현재 시각 반환.
    (POST 응답에는 날짜 없이 시각만 오므로 오늘 날짜 보정.)
    """
    today_str = datetime.now(KST).strftime("%Y%m%d")
    date_str = ord_dt if ord_dt else today_str
    time_str = ord_tmd.zfill(6) if ord_tmd else "000000"

    try:
        dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
        return KST.localize(dt)
    except ValueError:
        # 파싱 실패 시 현재 시각으로 대체 (주문 자체는 성공했으므로 전파 안 함)
        return datetime.now(KST)


def parse_order_response(
    response: KisResponse,
    *,
    code: str,
    side: OrderSide,
    order_type: OrderType,
    quantity: int,
    price: int,
    timestamp: datetime,
) -> OrderInfo:
    """
    매수/매도 POST 응답 → OrderInfo(status=ACCEPTED).

    KIS 응답 구조:
        output.ODNO     : 주문번호
        output.ORD_TMD  : 주문시각 (HHMMSS)

    Args:
        response:   KisClient.request_post() 결과 (rt_cd=0 보장)
        code:       주문한 종목코드 (응답에 없으므로 호출자가 전달)
        side:       OrderSide (호출자가 전달)
        order_type: OrderType (호출자가 전달)
        quantity:   주문수량 (호출자가 전달)
        price:      주문가격 (호출자가 전달)
        timestamp:  주문 시도 시각 (호출자가 전달, KST)

    Returns:
        OrderInfo(status=ACCEPTED, order_no=KIS주문번호)

    Raises:
        KisParseError: output이 dict가 아니거나 ODNO 누락
    """
    output = response.output
    if not isinstance(output, dict):
        raise KisParseError(
            f"주문 응답 output이 dict가 아님: {type(output).__name__}"
        )

    order_no = str(output.get("ODNO", "")).strip()
    if not order_no:
        raise KisParseError(
            f"주문 응답에 ODNO(주문번호) 없음. "
            f"output keys={list(output.keys())}"
        )

    ord_tmd = str(output.get("ORD_TMD", "")).strip()
    order_dt = _parse_order_datetime("", ord_tmd)

    return OrderInfo(
        code=code,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        status=OrderStatus.ACCEPTED,
        order_no=order_no,
        filled_qty=0,
        timestamp=order_dt,
        raw_response=dict(output),  # 원본 보존 (dict 복사)
    )


def parse_cancel_response(
    response: KisResponse,
    *,
    code: str,
    side: OrderSide,
    order_type: OrderType,
    quantity: int,
    price: int,
    timestamp: datetime,
) -> OrderInfo:
    """
    취소 POST 응답 → OrderInfo(status=CANCELLED).

    응답 구조는 매수/매도와 동일 (ODNO는 취소 주문번호).
    원래 주문번호가 아님에 주의 — 호출자(Order)가 관리.

    Raises:
        KisParseError: output 구조 불일치
    """
    output = response.output
    if not isinstance(output, dict):
        raise KisParseError(
            f"취소 응답 output이 dict가 아님: {type(output).__name__}"
        )

    cancel_order_no = str(output.get("ODNO", "")).strip()
    ord_tmd = str(output.get("ORD_TMD", "")).strip()
    cancel_dt = _parse_order_datetime("", ord_tmd)

    return OrderInfo(
        code=code,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        status=OrderStatus.CANCELLED,
        order_no=cancel_order_no if cancel_order_no else None,
        filled_qty=0,
        timestamp=cancel_dt,
        raw_response=dict(output),
    )


def _parse_order_status_from_qty(
    ord_qty: int,
    tot_ccld_qty: int,
    cncl_yn: str = "N",
) -> OrderStatus:
    """
    수량 및 취소여부로 OrderStatus 결정.

    Args:
        ord_qty:      주문수량
        tot_ccld_qty: 누적체결수량
        cncl_yn:      "Y"=취소됨

    Returns:
        CANCELLED / FILLED / PARTIAL / ACCEPTED
    """
    if cncl_yn == "Y":
        return OrderStatus.CANCELLED
    if tot_ccld_qty <= 0:
        return OrderStatus.ACCEPTED   # 미체결
    if tot_ccld_qty >= ord_qty:
        return OrderStatus.FILLED     # 전량체결
    return OrderStatus.PARTIAL        # 일부체결


def parse_pending_order_list(response: KisResponse) -> list[OrderInfo]:
    """
    미체결 조회 응답 → list[OrderInfo].

    KIS API: inquire-psbl-rvsecncl
    응답 구조: output (list)

    빈 리스트 응답 시 빈 list 반환 (예외 아님).

    Raises:
        KisParseError: output 구조 불일치
    """
    output = response.output
    if output is None or output == {} or output == []:
        return []
    if not isinstance(output, list):
        raise KisParseError(
            f"미체결 조회 output이 list가 아님: {type(output).__name__}"
        )

    result: list[OrderInfo] = []
    for idx, item in enumerate(output):
        if not isinstance(item, dict):
            raise KisParseError(
                f"미체결 항목[{idx}]이 dict가 아님: {type(item)}"
            )

        try:
            side = _to_order_side(str(item.get("sll_buy_dvsn_cd", "")))
        except KisParseError as e:
            raise KisParseError(
                f"미체결 항목[{idx}] 매수/매도 파싱 실패: {e}"
            ) from e

        order_type = _to_order_type(str(item.get("ord_dvsn_cd", "00")))

        ord_qty = _to_int(item.get("ord_qty"), "ord_qty")
        tot_ccld_qty = _to_int(item.get("tot_ccld_qty"), "tot_ccld_qty")
        ord_unpr = _to_int(item.get("ord_unpr"), "ord_unpr")

        # 미체결 API는 취소 주문을 포함하지 않으므로 cncl_yn 없음
        status = _parse_order_status_from_qty(
            ord_qty=ord_qty,
            tot_ccld_qty=tot_ccld_qty,
            cncl_yn="N",
        )

        order_no = str(item.get("odno", "")).strip()
        ord_dt = str(item.get("ord_dt", "")).strip()
        ord_tmd = str(item.get("ord_tmd", "")).strip()
        order_dt = _parse_order_datetime(ord_dt, ord_tmd)

        result.append(OrderInfo(
            code=str(item.get("pdno", "")).strip(),
            side=side,
            order_type=order_type,
            quantity=ord_qty,
            price=ord_unpr,
            status=status,
            order_no=order_no if order_no else None,
            filled_qty=tot_ccld_qty,
            timestamp=order_dt,
            raw_response=dict(item),
        ))

    return result


def parse_filled_order_list(response: KisResponse) -> list[OrderInfo]:
    """
    당일 체결 조회 응답 → list[OrderInfo].

    KIS API: inquire-daily-ccld
    응답 구조: output1 (list), output2 (요약 - 이 파서에서는 사용 안 함)

    빈 리스트 응답 시 빈 list 반환.

    Raises:
        KisParseError: output1 구조 불일치
    """
    output1 = response.output1
    if output1 is None or output1 == {} or output1 == []:
        return []
    if not isinstance(output1, list):
        raise KisParseError(
            f"체결 조회 output1이 list가 아님: {type(output1).__name__}"
        )

    result: list[OrderInfo] = []
    for idx, item in enumerate(output1):
        if not isinstance(item, dict):
            raise KisParseError(
                f"체결 항목[{idx}]이 dict가 아님: {type(item)}"
            )

        try:
            side = _to_order_side(str(item.get("sll_buy_dvsn_cd", "")))
        except KisParseError as e:
            raise KisParseError(
                f"체결 항목[{idx}] 매수/매도 파싱 실패: {e}"
            ) from e

        order_type = _to_order_type(str(item.get("ord_dvsn_cd", "00")))

        ord_qty = _to_int(item.get("ord_qty"), "ord_qty")
        tot_ccld_qty = _to_int(item.get("tot_ccld_qty"), "tot_ccld_qty")
        ord_unpr = _to_int(item.get("ord_unpr"), "ord_unpr")
        cncl_yn = str(item.get("cncl_yn", "N")).strip().upper()

        status = _parse_order_status_from_qty(
            ord_qty=ord_qty,
            tot_ccld_qty=tot_ccld_qty,
            cncl_yn=cncl_yn,
        )

        order_no = str(item.get("odno", "")).strip()
        ord_dt = str(item.get("ord_dt", "")).strip()
        ord_tmd = str(item.get("ord_tmd", "")).strip()
        order_dt = _parse_order_datetime(ord_dt, ord_tmd)

        result.append(OrderInfo(
            code=str(item.get("pdno", "")).strip(),
            side=side,
            order_type=order_type,
            quantity=ord_qty,
            price=ord_unpr,
            status=status,
            order_no=order_no if order_no else None,
            filled_qty=tot_ccld_qty,
            timestamp=order_dt,
            raw_response=dict(item),
        ))

    return result
