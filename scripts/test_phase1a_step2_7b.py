"""
Phase 1-A Step 2-7-B 검증.

목적:
    일봉/분봉 조회 검증. parsers 단위 테스트 + 실제 KIS 호출.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


def main() -> int:
    print("=" * 60)
    print(" Phase 1-A Step 2-7-B 검증")
    print("=" * 60)

    print("\n[0] 준비")
    import pandas as pd
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisAuth, KisClient, KisResponse, Quote
    from broker.kis.parsers import (
        parse_daily_candles, parse_minute_candles,
        CANDLE_COLUMNS, _empty_candle_df,
    )

    settings = load_settings()
    setup_logging(settings)
    _ok("준비 완료")

    # ------------------------------------------------------------
    # 1. 빈 DataFrame 스키마
    # ------------------------------------------------------------
    print("\n[1] 빈 DataFrame 스키마")
    empty = _empty_candle_df()
    if list(empty.columns) != CANDLE_COLUMNS:
        _fail("컬럼", str(list(empty.columns)))
        return 1
    if len(empty) != 0:
        _fail("행 수", str(len(empty)))
        return 1
    _ok("스키마 + 0행")

    # ------------------------------------------------------------
    # 2. parse_daily_candles (단위)
    # ------------------------------------------------------------
    print("\n[2] parse_daily_candles")
    fake_daily = KisResponse(
        body={
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output2": [
                # KIS는 최신순
                {"stck_bsop_date": "20260410", "stck_oprc": "206000",
                 "stck_hgpr": "211000", "stck_lwpr": "205500",
                 "stck_clpr": "208500", "acml_vol": "16000000"},
                {"stck_bsop_date": "20260409", "stck_oprc": "200000",
                 "stck_hgpr": "205000", "stck_lwpr": "199000",
                 "stck_clpr": "204000", "acml_vol": "12000000"},
                {"stck_bsop_date": "20260408", "stck_oprc": "195000",
                 "stck_hgpr": "201000", "stck_lwpr": "194000",
                 "stck_clpr": "200000", "acml_vol": "11000000"},
            ],
        },
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="FHKST03010100", http_status=200,
    )
    df = parse_daily_candles(fake_daily)

    if len(df) != 3:
        _fail("행 수", str(len(df)))
        return 1
    if list(df.columns) != CANDLE_COLUMNS:
        _fail("컬럼", str(list(df.columns)))
        return 1
    # 오름차순 확인
    dates = [d.strftime("%Y%m%d") for d in df["datetime"]]
    if dates != ["20260408", "20260409", "20260410"]:
        _fail("정렬 (과거→현재)", str(dates))
        return 1
    _ok("3행, 과거→현재 오름차순")

    # 값 확인
    row0 = df.iloc[0]
    if row0["open"] != 195000 or row0["close"] != 200000:
        _fail("첫 행 값", f"open={row0['open']}, close={row0['close']}")
        return 1
    _ok("값 매핑 정확")

    # 타입
    if str(df["close"].dtype) != "int64":
        _fail("close dtype", str(df["close"].dtype))
        return 1
    _ok("int64 타입")

    # ------------------------------------------------------------
    # 3. parse_minute_candles (단위)
    # ------------------------------------------------------------
    print("\n[3] parse_minute_candles")
    fake_min = KisResponse(
        body={
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output2": [
                {"stck_bsop_date": "20260410", "stck_cntg_hour": "152500",
                 "stck_oprc": "206000", "stck_hgpr": "206500",
                 "stck_lwpr": "205800", "stck_prpr": "206300",
                 "cntg_vol": "12000"},
                {"stck_bsop_date": "20260410", "stck_cntg_hour": "152400",
                 "stck_oprc": "205500", "stck_hgpr": "206100",
                 "stck_lwpr": "205500", "stck_prpr": "206000",
                 "cntg_vol": "8000"},
            ],
        },
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="FHKST03010200", http_status=200,
    )
    df_m = parse_minute_candles(fake_min)
    if len(df_m) != 2:
        _fail("분봉 행 수", str(len(df_m)))
        return 1
    # 오름차순: 15:24 → 15:25
    times = [d.strftime("%H%M") for d in df_m["datetime"]]
    if times != ["1524", "1525"]:
        _fail("분봉 정렬", str(times))
        return 1
    _ok("분봉 2행, 오름차순")

    # ------------------------------------------------------------
    # 4. 빈 output2
    # ------------------------------------------------------------
    print("\n[4] 빈 output2 → 빈 DataFrame")
    empty_resp = KisResponse(
        body={"rt_cd": "0", "msg_cd": "X", "msg1": "OK", "output2": []},
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="X", http_status=200,
    )
    df_empty = parse_daily_candles(empty_resp)
    if len(df_empty) != 0 or list(df_empty.columns) != CANDLE_COLUMNS:
        _fail("빈 일봉", str(df_empty))
        return 1
    _ok("빈 일봉 → 빈 DataFrame (스키마 유지)")

    # ------------------------------------------------------------
    # 5. Quote 입력값 검증
    # ------------------------------------------------------------
    print("\n[5] Quote 입력값 검증")
    auth = KisAuth(settings)
    client = KisClient(settings, auth)
    quote = Quote(client)

    # count 범위
    try:
        quote.get_daily_candles("005930", count=0)
        _fail("count=0", "통과됨")
        return 1
    except ValueError:
        pass
    try:
        quote.get_daily_candles("005930", count=101)
        _fail("count=101", "통과됨")
        return 1
    except ValueError:
        pass
    _ok("count 범위 거부 (0, 101)")

    # interval 검증
    try:
        quote.get_minute_candles("005930", interval="5")
        _fail("interval=5", "통과됨")
        return 1
    except ValueError:
        pass
    _ok("interval='5' 거부 (1분봉만 지원)")

    # ------------------------------------------------------------
    # 6. 실제 KIS 호출 - 일봉
    # ------------------------------------------------------------
    print("\n[6] 삼성전자 일봉 30개 (실제 호출)")
    try:
        df = quote.get_daily_candles("005930", count=30)
    except Exception as e:
        _fail("일봉 호출", f"{type(e).__name__}: {e}")
        return 1

    if len(df) == 0:
        _fail("일봉 결과", "0행")
        return 1
    _ok("일봉 DataFrame", f"{len(df)}행")

    # 컬럼/타입
    if list(df.columns) != CANDLE_COLUMNS:
        _fail("컬럼", str(list(df.columns)))
        return 1
    _ok("컬럼 스키마")

    # 오름차순
    dts = df["datetime"].tolist()
    if dts != sorted(dts):
        _fail("정렬", "오름차순 아님")
        return 1
    _ok("오름차순 정렬")

    # 합리성
    if (df["high"] < df["low"]).any():
        _fail("high >= low", "위반")
        return 1
    if (df["close"] <= 0).any():
        _fail("close > 0", "위반")
        return 1
    _ok("OHLC 합리성")

    # 샘플 출력
    print(f"        첫 행: {df.iloc[0]['datetime'].date()} close={df.iloc[0]['close']:,}")
    print(f"        끝 행: {df.iloc[-1]['datetime'].date()} close={df.iloc[-1]['close']:,}")

    # ------------------------------------------------------------
    # 7. 실제 KIS 호출 - 분봉
    # ------------------------------------------------------------
    # 레이트리밋 안전 마진 (일봉 호출 직후라 추가 대기)
    import time as _t; 
    _t.sleep(2.0)

    print("\n[7] 삼성전자 분봉 (실제 호출)")
    df_m = None
    last_err = None
    for retry in range(3):
        try:
            df_m = quote.get_minute_candles("005930", interval="1")
            break
        except Exception as e:
            last_err = e
            msg = str(e)
            if "EGW00201" in msg or "초당" in msg:
                print(f"        레이트리밋 감지, 2초 후 재시도 ({retry + 1}/3)")
                _t.sleep(2.0)
                continue
            _fail("분봉 호출", f"{type(e).__name__}: {e}")
            return 1
    if df_m is None:
        _fail("분봉 호출", f"3회 재시도 실패: {last_err}")
        return 1
    client.close()

    print()
    print("=" * 60)
    print(" Step 2-7-B 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-7-C (account + broker Facade)")
    return 0


if __name__ == "__main__":
    sys.exit(main())