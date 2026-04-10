"""
KIS 응답을 담는 데이터 모델.

설계 원칙:
    - frozen dataclass: 생성 후 변경 금지 (자동매매 안정성).
    - 가격/수량은 int (국내주식은 원/주 단위, 소수점 없음).
    - 비율(%)/평균가는 float.
    - 시각은 항상 timezone-aware (KST).
    - 캔들 데이터는 DataFrame으로 별도 반환 (이 파일엔 없음).

Step 1에서는 정의만. Step 3/4에서 parsers가 채워서 반환.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PriceSnapshot:
    """현재가 스냅샷."""

    code: str           # 6자리 종목코드
    name: str           # 종목명 (없으면 빈 문자열)
    price: int          # 현재가
    open: int           # 시가
    high: int           # 고가
    low: int            # 저가
    prev_close: int     # 전일 종가
    change: int         # 전일대비 (음수 가능)
    change_rate: float  # 등락률 (%, 음수 가능)
    volume: int         # 누적 거래량
    timestamp: datetime  # 조회 시각 (KST, tz-aware)


@dataclass(frozen=True)
class Holding:
    """보유 종목 1개."""

    code: str
    name: str
    quantity: int        # 보유 수량
    available: int       # 매도 가능 수량
    avg_price: float     # 평균 매입가
    current_price: int   # 현재가
    eval_amount: int     # 평가 금액
    profit: int          # 평가 손익 (음수 가능)
    profit_rate: float   # 수익률 (%, 음수 가능)


@dataclass(frozen=True)
class Balance:
    """계좌 잔고 + 보유 종목."""

    cash: int                  # 예수금
    available_cash: int        # 주문 가능 금액
    total_eval: int            # 총평가금액 (예수금 + 평가금액)
    total_profit: int          # 총평가손익
    holdings: tuple[Holding, ...] = field(default_factory=tuple)
    has_more_pages: bool = False  # 페이징 잔여 여부 (R11)
    timestamp: datetime = field(
        default_factory=lambda: datetime.now()
    )

    def find(self, code: str) -> Holding | None:
        """종목코드로 보유 종목 검색. 없으면 None."""
        for h in self.holdings:
            if h.code == code:
                return h
        return None

    @property
    def holding_count(self) -> int:
        """보유 종목 수."""
        return len(self.holdings)
    
@dataclass(frozen=True)
class KisResponse:
    """
    KisClient가 반환하는 표준 응답 컨테이너.

    parsers 모듈이 이 객체를 받아서 도메인 모델
    (PriceSnapshot, Balance, DataFrame 등)로 변환한다.

    설계 원칙:
        - body는 KIS 응답 JSON 본문 그대로 (수정 금지).
        - frozen이지만 body(dict)의 내용은 mutable이므로
          호출자(parsers)는 body를 절대 수정하지 않는다.
        - rt_cd가 "0"이 아닌 응답은 KisClient에서 이미 KisApiError로
          전환했으므로, 이 객체는 항상 성공 응답만 담는다.

    필드:
        body: KIS 응답 JSON 본문 전체
        rt_cd: 항상 "0" (성공 응답만 도달)
        msg_cd: KIS 메시지 코드 (성공 시에도 정보성 코드 있음)
        msg: KIS 메시지 본문 (성공 시 "정상 처리" 등)
        tr_cont: 페이징 토큰 ("F"=다음있음(첫조회) / "M"=다음있음 /
                              "D"=마지막 / "E"=마지막 / ""=단일조회)
        tr_id: 실제로 호출된 TR_ID (모의 변환 후 값)
        http_status: HTTP 상태 코드 (항상 200)
    """

    body: dict
    rt_cd: str
    msg_cd: str
    msg: str
    tr_cont: str
    tr_id: str
    http_status: int

    @property
    def output(self) -> dict | list:
        """
        대부분의 KIS 시세/단일조회 응답이 사용하는 'output' 필드.

        없으면 빈 dict 반환 (KeyError 방지).
        호출자는 어떤 타입(dict/list)이 오는지 TR별로 알고 있어야 한다.
        """
        return self.body.get("output", {})

    @property
    def output1(self) -> dict | list:
        """
        잔고 조회 등 복합 응답에서 사용하는 'output1' 필드.

        예: 주식잔고조회는 output1=보유종목 리스트, output2=잔고 요약.
        """
        return self.body.get("output1", {})

    @property
    def output2(self) -> dict | list:
        """잔고 조회 등 복합 응답에서 사용하는 'output2' 필드."""
        return self.body.get("output2", {})

    @property
    def has_more_pages(self) -> bool:
        """
        페이징 다음 데이터 존재 여부.

        KIS 페이징 규칙 (response header tr_cont):
            "F" : 다음 페이지 있음 (첫 조회의 응답)
            "M" : 다음 페이지 있음 (연속 조회 중)
            "D" : 마지막 페이지
            "E" : 마지막 페이지
            ""  : 단일 조회 (페이징 없음)

        다음 페이지 요청 시 호출자는 request_get(tr_cont="N", ...) 으로
        호출하면 된다.
        """
        return self.tr_cont in ("F", "M")
    
    