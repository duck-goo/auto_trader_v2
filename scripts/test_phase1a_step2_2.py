"""
Phase 1-A Step 2-2 검증.

목적:
    KisResponse dataclass가 정상 동작하고 export가 갱신되었는지 확인.
    네트워크 호출 없음.

검증 항목:
    1. import / export
    2. 필수 필드 + frozen 동작
    3. output / output1 / output2 프로퍼티 (정상 케이스)
    4. output 프로퍼티 (필드 누락 시 빈 dict)
    5. has_more_pages 프로퍼티 (5가지 tr_cont 값)
    6. 기존 모델 회귀 (PriceSnapshot/Holding/Balance)

실행:
    python scripts/test_phase1a_step2_2.py
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
    print(" Phase 1-A Step 2-2 검증")
    print("=" * 60)

    # ------------------------------------------------------------
    # 1. import / export
    # ------------------------------------------------------------
    print("\n[1] import / export")
    try:
        from broker.kis import KisResponse
        from broker.kis.models import KisResponse as KR_direct
    except ImportError as e:
        _fail("KisResponse import", str(e))
        return 1
    if KisResponse is not KR_direct:
        _fail("동일 클래스", "broker.kis vs broker.kis.models")
        return 1
    _ok("KisResponse import (양 경로 동일)")

    import broker.kis as kis_pkg
    if "KisResponse" not in dir(kis_pkg):
        _fail("__all__ 노출", "KisResponse가 export 안됨")
        return 1
    _ok("__all__ export")

    # ------------------------------------------------------------
    # 2. 생성 + 필드 + frozen
    # ------------------------------------------------------------
    print("\n[2] 생성 / 필드 / frozen")
    body_sample = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상 처리 되었습니다.",
        "output": {
            "stck_prpr": "70000",
            "stck_oprc": "69500",
        },
    }
    try:
        resp = KisResponse(
            body=body_sample,
            rt_cd="0",
            msg_cd="MCA00000",
            msg="정상 처리 되었습니다.",
            tr_cont="",
            tr_id="FHKST01010100",
            http_status=200,
        )
    except Exception as e:
        _fail("KisResponse 생성", str(e))
        return 1
    _ok("KisResponse 생성")

    # frozen
    try:
        resp.rt_cd = "1"  # type: ignore[misc]
        _fail("frozen", "필드 변경이 허용됨")
        return 1
    except Exception:
        _ok("frozen", "필드 변경 차단")

    # ------------------------------------------------------------
    # 3. output 프로퍼티 (정상)
    # ------------------------------------------------------------
    print("\n[3] output 프로퍼티 (정상 응답)")
    out = resp.output
    if not isinstance(out, dict):
        _fail("output 타입", f"dict 아님: {type(out)}")
        return 1
    if out.get("stck_prpr") != "70000":
        _fail("output 내용", str(out))
        return 1
    _ok("output (dict, 값 일치)")

    # ------------------------------------------------------------
    # 4. output 누락 케이스 → 빈 dict
    # ------------------------------------------------------------
    print("\n[4] output 누락 케이스")
    resp_empty = KisResponse(
        body={"rt_cd": "0", "msg_cd": "X", "msg1": "Y"},
        rt_cd="0", msg_cd="X", msg="Y",
        tr_cont="", tr_id="ZZZ", http_status=200,
    )
    if resp_empty.output != {}:
        _fail("output 기본값", f"빈 dict 아님: {resp_empty.output}")
        return 1
    if resp_empty.output1 != {}:
        _fail("output1 기본값", str(resp_empty.output1))
        return 1
    if resp_empty.output2 != {}:
        _fail("output2 기본값", str(resp_empty.output2))
        return 1
    _ok("output/output1/output2 누락 시 빈 dict")

    # ------------------------------------------------------------
    # 5. has_more_pages
    # ------------------------------------------------------------
    print("\n[5] has_more_pages (페이징 토큰)")
    cases = [
        ("F", True),   # 다음 있음 (첫 조회)
        ("M", True),   # 다음 있음 (연속)
        ("D", False),  # 마지막
        ("E", False),  # 마지막
        ("",  False),  # 단일 조회
    ]
    for tr_cont, expected in cases:
        r = KisResponse(
            body={}, rt_cd="0", msg_cd="", msg="",
            tr_cont=tr_cont, tr_id="X", http_status=200,
        )
        if r.has_more_pages != expected:
            _fail(
                f"has_more_pages('{tr_cont}')",
                f"기대={expected}, 실제={r.has_more_pages}",
            )
            return 1
    _ok("has_more_pages", "5가지 케이스 (F/M/D/E/'') 모두 정확")

    # ------------------------------------------------------------
    # 6. output1 / output2 (잔고 조회 시뮬레이션)
    # ------------------------------------------------------------
    print("\n[6] output1 / output2 (복합 응답)")
    balance_body = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상",
        "output1": [  # 보유 종목 리스트
            {"pdno": "005930", "hldg_qty": "10"},
            {"pdno": "000660", "hldg_qty": "5"},
        ],
        "output2": [  # 잔고 요약
            {"dnca_tot_amt": "1000000"}
        ],
    }
    r = KisResponse(
        body=balance_body, rt_cd="0", msg_cd="MCA00000", msg="정상",
        tr_cont="", tr_id="VTTC8434R", http_status=200,
    )
    if not isinstance(r.output1, list) or len(r.output1) != 2:
        _fail("output1 (list)", str(r.output1))
        return 1
    if not isinstance(r.output2, list) or len(r.output2) != 1:
        _fail("output2 (list)", str(r.output2))
        return 1
    _ok("output1 (list[2]) / output2 (list[1])")

    # ------------------------------------------------------------
    # 7. 기존 모델 회귀
    # ------------------------------------------------------------
    print("\n[7] 기존 모델 회귀")
    from datetime import datetime
    import pytz
    from broker.kis import Balance, Holding, PriceSnapshot

    KST = pytz.timezone("Asia/Seoul")
    snap = PriceSnapshot(
        code="005930", name="삼성전자",
        price=70000, open=69500, high=70500, low=69000,
        prev_close=69800, change=200, change_rate=0.29,
        volume=1234567, timestamp=datetime.now(KST),
    )
    h = Holding(
        code="005930", name="삼성전자",
        quantity=10, available=10, avg_price=68000.0,
        current_price=70000, eval_amount=700000,
        profit=20000, profit_rate=2.94,
    )
    bal = Balance(
        cash=1_000_000, available_cash=950_000,
        total_eval=1_700_000, total_profit=20_000,
        holdings=(h,), timestamp=datetime.now(KST),
    )
    if bal.find("005930") is not h:
        _fail("Balance.find", "회귀")
        return 1
    if snap.price != 70000:
        _fail("PriceSnapshot.price", "회귀")
        return 1
    _ok("PriceSnapshot / Holding / Balance 회귀 없음")

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(" Step 2-2 모든 검증 통과")
    print("=" * 60)
    print(" 다음: Step 2-3 (client.py 골격 + TR_ID 변환 + 헤더)")
    return 0


if __name__ == "__main__":
    sys.exit(main())