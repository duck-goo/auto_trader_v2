"""
브로커 추상 인터페이스.

향후 KIS 외 다른 증권사(키움, 넥스트레이드 등) 확장 시
이 인터페이스를 구현하면 된다.

Phase 1-A: 조회(read-only) 메서드 4개
Phase 1-B: 주문 메서드 3개 (place_order, cancel_order, get_order_status)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from broker.kis.models import Balance, PriceSnapshot


class BrokerInterface(ABC):
    """
    모든 브로커 구현체가 따라야 할 인터페이스.

    Phase 1-A에서 구현된 메서드:
        - get_access_token()         인증 (Phase 0)
        - get_current_price(code)    현재가
        - get_daily_candles(...)     일봉
        - get_minute_candles(...)    분봉
        - get_balance()              잔고

    Phase 1-B에서 추가될 메서드:
        - place_order(...)
        - cancel_order(...)
        - get_order_status(...)
    """

    @abstractmethod
    def get_access_token(self) -> str:
        """유효한 access token 반환. 없거나 만료 시 갱신."""
        raise NotImplementedError

    @abstractmethod
    def get_current_price(self, code: str) -> "PriceSnapshot":
        """
        주식 현재가 조회.

        Args:
            code: 6자리 종목코드

        Returns:
            PriceSnapshot
        """
        raise NotImplementedError

    @abstractmethod
    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> "pd.DataFrame":
        """
        일봉 조회 (과거 → 현재 오름차순).

        Args:
            code: 6자리 종목코드
            count: 개수 (1~100)
            end_date: 기준일 YYYYMMDD (None이면 오늘)
        """
        raise NotImplementedError

    @abstractmethod
    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> "pd.DataFrame":
        """
        분봉 조회.

        Args:
            code: 6자리 종목코드
            interval: "1" (1분봉). 다른 봉은 호출자가 리샘플링.
        """
        raise NotImplementedError

    @abstractmethod
    def get_balance(self) -> "Balance":
        """
        계좌 잔고 + 보유종목 조회.

        Returns:
            Balance
        """
        raise NotImplementedError

    def close(self) -> None:
        """
        리소스 정리. 프로그램 종료 시 호출.

        구현체가 오버라이드. 기본은 no-op.
        """
        pass