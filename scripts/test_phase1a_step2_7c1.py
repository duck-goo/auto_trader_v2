"""
Phase 1-A Step 2-7-C-1 검증.

목적:
    parse_balance + Account.get_balance() 검증.
    단위 테스트 + 실제 KIS 모의계좌 호출.
"""

from __future__ import annotations

import sys
import time as _t
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
    print(" Phase 1-A Step 2-7-C-1 검증 (잔고 조회)")
    print("=" * 60)

    print("\n[0] 준비")
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import (
        Account, Balance, Holding, KisAuth, KisClient, KisResponse,
    )
    from broker.kis.parsers import parse_balance
    from broker.kis.errors import KisParseError

    settings = load_settings()
    setup_logging(settings)
    if settings.mode != "mock":
        _fail("mode", "mock 전용")
        return 1
    _ok("준비 완료")

    # ------------------------------------------------------------
    # 1. parse_balance 단위 - 빈 계좌
    # ------------------------------------------------------------
    print("\n[1] parse_balance 단위 - 빈 계좌")
    empty_resp = KisResponse(
        body={
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output1": [],
            "output2": [{
                "dnca_tot_amt": "10000000",
                "prvs_rcdl_excc_amt": "10000000",
                "tot_evlu_amt": "10000000",
                "evlu_pfls_smtl_amt": "0",
            }],
        },
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="VTTC8434R", http_status=200,
    )
    bal = parse_balance(empty_resp)
    if bal.cash != 10000000:
        _fail("cash", str(bal.cash))
        return 1
    if bal.holding_count != 0:
        _fail("holding_count", str(bal.holding_count))
        return 1
    if bal.has_more_pages:
        _fail("has_more_pages", "False여야")
        return 1
    _ok("빈 계좌", f"cash={bal.cash:,}, holdings=0")

    # ------------------------------------------------------------
    # 2. parse_balance - 보유 종목 2개
    # ------------------------------------------------------------
    print("\n[2] parse_balance - 보유 2종목")
    resp = KisResponse(
        body={
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output1": [
                {
                    "pdno": "005930", "prdt_name": "삼성전자",
                    "hldg_qty": "10", "ord_psbl_qty": "10",
                    "pchs_avg_pric": "68000.0", "prpr": "70000",
                    "evlu_amt": "700000", "evlu_pfls_amt": "20000",
                    "evlu_pfls_rt": "2.94",
                },
                {
                    "pdno": "000660", "prdt_name": "SK하이닉스",
                    "hldg_qty": "5", "ord_psbl_qty": "5",
                    "pchs_avg_pric": "120000.0", "prpr": "125000",
                    "evlu_amt": "625000", "evlu_pfls_amt": "25000",
                    "evlu_pfls_rt": "4.17",
                },
                # 수량 0 종목 (필터링되어야 함)
                {
                    "pdno": "000000", "prdt_name": "테스트",
                    "hldg_qty": "0", "ord_psbl_qty": "0",
                    "pchs_avg_pric": "0", "prpr": "0",
                    "evlu_amt": "0", "evlu_pfls_amt": "0",
                    "evlu_pfls_rt": "0",
                },
            ],
            "output2": [{
                "dnca_tot_amt": "1000000",
                "prvs_rcdl_excc_amt": "950000",
                "tot_evlu_amt": "2325000",
                "evlu_pfls_smtl_amt": "45000",
            }],
        },
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="VTTC8434R", http_status=200,
    )
    bal = parse_balance(resp)

    if bal.holding_count != 2:
        _fail("수량0 필터", f"{bal.holding_count}개 (기대: 2)")
        return 1
    _ok("수량0 종목 필터링", "3개 중 2개만 포함")

    ss = bal.find("005930")
    if ss is None:
        _fail("find", "삼성전자 없음")
        return 1
    if ss.quantity != 10 or ss.current_price != 70000:
        _fail("삼성전자 필드", f"qty={ss.quantity}, price={ss.current_price}")
        return 1
    _ok("Holding 필드 매핑", "삼성전자 정확")

    if bal.cash != 1_000_000 or bal.available_cash != 950_000:
        _fail("cash/available", f"{bal.cash}/{bal.available_cash}")
        return 1
    _ok("예수금/주문가능")

    # ------------------------------------------------------------
    # 3. parse_balance - output2 빈 배열
    # ------------------------------------------------------------
    print("\n[3] output2 빈 배열 → 0으로 채움")
    resp = KisResponse(
        body={
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output1": [],
            "output2": [],
        },
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="", tr_id="VTTC8434R", http_status=200,
    )
    bal = parse_balance(resp)
    if bal.cash != 0 or bal.total_eval != 0:
        _fail("빈 output2", f"cash={bal.cash}")
        return 1
    _ok("output2 빈 배열 안전 처리")

    # ------------------------------------------------------------
    # 4. has_more_pages 전파
    # ------------------------------------------------------------
    print("\n[4] tr_cont='M' → has_more_pages=True")
    resp = KisResponse(
        body={
            "rt_cd": "0", "msg_cd": "X", "msg1": "OK",
            "output1": [], "output2": [],
        },
        rt_cd="0", msg_cd="X", msg="OK",
        tr_cont="M", tr_id="VTTC8434R", http_status=200,
    )
    bal = parse_balance(resp)
    if not bal.has_more_pages:
        _fail("has_more_pages", "False")
        return 1
    _ok("has_more_pages=True 전파")

    # ------------------------------------------------------------
    # 5. 실제 KIS 호출 - 모의계좌 잔고
    # ------------------------------------------------------------
    print("\n[5] 모의계좌 실제 잔고 조회")
    _t.sleep(2.0)  # 이전 테스트와 간격 확보 (레이트리밋 방지)

    auth = KisAuth(settings)
    client = KisClient(settings, auth)
    account = Account(client, settings)

    try:
        balance = account.get_balance()
    except Exception as e:
        _fail("get_balance", f"{type(e).__name__}: {e}")
        return 1

    if not isinstance(balance, Balance):
        _fail("타입", str(type(balance)))
        return 1
    _ok("Balance 반환")

    # 합리성: 숫자 필드는 음수 아님 (손익 제외)
    if balance.cash < 0:
        _fail("cash >= 0", str(balance.cash))
        return 1
    if balance.available_cash < 0:
        _fail("available_cash >= 0", str(balance.available_cash))
        return 1

    print(f"        예수금:      {balance.cash:,}원")
    print(f"        주문가능:    {balance.available_cash:,}원")
    print(f"        총평가액:    {balance.total_eval:,}원")
    print(f"        총손익:      {balance.total_profit:+,}원")
    print(f"        보유종목:    {balance.holding_count}개")
    print(f"        페이징 더?:  {balance.has_more_pages}")

    if balance.holding_count > 0:
        print(f"        [보유종목]")
        for h in balance.holdings[:5]:  # 최대 5개만 출력
            print(
                f"          {h.code} {h.name}: "
                f"{h.quantity}주 @ {h.avg_price:,.0f}원 → "
                f"현재 {h.current_price:,}원 "
                f"({h.profit:+,}원, {h.profit_rate:+.2f}%)"
            )
        if balance.holding_count > 5:
            print(f"          ... +{balance.holding_count - 5}종목")

    _ok("실제 잔고 조회 성공")

    client.close()

    print()
    print("=" * 60)
    print(" Step 2-7-C-1 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-7-C-2 (KisBroker Facade + base.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())