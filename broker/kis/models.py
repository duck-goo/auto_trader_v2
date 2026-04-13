"""
KIS 응답을 담는 데이터 모델.

설계 원칙:
    - frozen dataclass: 생성 후 변경 금지 (자동매매 안정성).
    - 가격/수량은 int (국내주식은 원/주 단위, 소수점 없음).
    - 비율(%)/평균가는 float.
    - 시각은 항상 timezone-aware (KST).
    - 캔들 데이터는 DataFrame으로 별도 반환 (이 파일엔 없음).

Phase 1-A: PriceSnapshot, Holding, Balance, KisResponse
Phase 1-B: OrderSide, OrderType, OrderStatus, OrderInfo 추가
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


# ============================================================
# Phase 1-A 모델 (변경 없음)
# ============================================================

@dataclass(frozen=True)
class PriceSnapshot:
    """현재가 스냅샷."""
    code: str
    name: str
    price: int
    open: int
    high: int
    low: int
    prev_close: int
    change: int
    change_rate: float
    volume: int
    timestamp: datetime


@dataclass(frozen=True)
class Holding:
    """보유 종목 1개."""
    code: str
    name: str
    quantity: int
    available: int
    avg_price: float
    current_price: int
    eval_amount: int
    profit: int
    profit_rate: float


@dataclass(frozen=True)
class Balance:
    """계좌 잔고 + 보유 종목."""
    cash: int
    available_cash: int
    total_eval: int
    total_profit: int
    holdings: tuple[Holding, ...] = field(default_factory=tuple)
    has_more_pages: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now())

    def find(self, code: str) -> Holding | None:
        for h in self.holdings:
            if h.code == code:
                return h
        return None

    @property
    def holding_count(self) -> int:
        return len(self.holdings)


@dataclass(frozen=True)
class KisResponse:
    """KisClient가 반환하는 표준 응답 컨테이너."""
    body: dict
    rt_cd: str
    msg_cd: str
    msg: str
    tr_cont: str
    tr_id: str
    http_status: int

    @property
    def output(self) -> dict | list:
        return self.body.get("output", {})

    @property
    def output1(self) -> dict | list:
        return self.body.get("output1", {})

    @property
    def output2(self) -> dict | list:
        return self.body.get("output2", {})

    @property
    def has_more_pages(self) -> bool:
        return self.tr_cont in ("F", "M")


# ============================================================
# Phase 1-B 모델 (신규)
# ============================================================

class OrderSide(str, enum.Enum):
    """
    매수/매도 구분.

    str 상속: "buy" == OrderSide.BUY 비교 가능 (전략 코드 편의).
    """
    BUY  = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    """
    주문 유형.

    MARKET: 시장가. place_order(price=0) 시 자동 선택.
    LIMIT:  지정가. place_order(price>0) 시 자동 선택.
    """
    MARKET = "market"
    LIMIT  = "limit"


class OrderStatus(str, enum.Enum):
    """
    주문 상태.

    상태 전이 다이어그램:
        PENDING → ACCEPTED  : POST rt_cd=0, 주문번호 수신
        PENDING → REJECTED  : POST rt_cd≠0 (잔량 부족, 거래시간 외 등)
        PENDING → UNKNOWN   : POST 네트워크 예외 (재시도 금지)
        ACCEPTED → FILLED   : 체결 조회로 전량 체결 확인
        ACCEPTED → PARTIAL  : 체결 조회로 일부 체결 확인
        ACCEPTED → CANCELLED: 취소 완료 확인

    UNKNOWN 주의:
        재시도 절대 금지. 호출자가 get_order_status()로
        실제 접수 여부를 확인한 후 판단해야 한다.
    """
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    FILLED    = "filled"
    PARTIAL   = "partial"
    CANCELLED = "cancelled"
    REJECTED  = "rejected"
    UNKNOWN   = "unknown"


@dataclass(frozen=True)
class OrderInfo:
    """
    주문 1건의 불변 스냅샷.

    frozen=True 이유:
        상태가 바뀌면(체결, 취소) 새 OrderInfo를 생성한다.
        이는 Phase 2 DB의 이벤트 소싱 방식과 일치한다.

    order_no:
        ACCEPTED 이후에만 값이 있음.
        PENDING/UNKNOWN/REJECTED 상태에서는 None.

    raw_response:
        KIS 응답 원본. 디버깅 및 감사 로그용.
        주문 성공 시 output dict, 실패/네트워크 오류 시 빈 dict.
    """
    code: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: int          # 시장가=0, 지정가=실제 가격
    status: OrderStatus
    order_no: str | None  # KIS 주문번호. ACCEPTED 이후에만 있음.
    filled_qty: int     # 체결 수량 (미체결=0)
    timestamp: datetime  # 주문 시도 시각 (KST)
    raw_response: dict  # KIS 원본 응답 (감사 로그용)