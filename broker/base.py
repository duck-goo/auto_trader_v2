"""
브로커 추상 인터페이스.

Phase 1-A: 조회(read-only) 메서드 4개
Phase 1-B: 주문 메서드 3개 추가
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from broker.kis.models import Balance, OrderInfo, PriceSnapshot


class BrokerInterface(ABC):

    @abstractmethod
    def get_access_token(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_current_price(self, code: str) -> "PriceSnapshot":
        raise NotImplementedError

    @abstractmethod
    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> "pd.DataFrame":
        raise NotImplementedError

    @abstractmethod
    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> "pd.DataFrame":
        raise NotImplementedError

    @abstractmethod
    def get_balance(self) -> "Balance":
        raise NotImplementedError

    # ============================================================
    # Phase 1-B: 주문 메서드
    # ============================================================

    @abstractmethod
    def place_order(
        self,
        code: str,
        side: str,
        quantity: int,
        price: int = 0,
    ) -> "OrderInfo":
        """
        주문 전송.

        Args:
            code:     6자리 종목코드
            side:     "buy" | "sell"
            quantity: 1 이상 정수
            price:    0=시장가, >0=지정가

        Returns:
            OrderInfo (status=ACCEPTED 또는 UNKNOWN)

        Raises:
            ValueError:      입력값 형식 오류 (코드, 수량, side)
            KisOrderError:   중복 주문 감지
            KisApiError:     KIS 거부 (rt_cd≠0) → status=REJECTED
            KisApiError:     POST 네트워크 실패 → status=UNKNOWN
        """
        raise NotImplementedError

    @abstractmethod
    def cancel_order(
        self,
        order_no: str,
        code: str,
        quantity: int,
    ) -> "OrderInfo":
        """
        주문 전량 취소.

        Args:
            order_no: KIS 주문번호 (place_order 반환값의 order_no)
            code:     6자리 종목코드
            quantity: 취소할 수량 (= 원래 주문 수량)

        Returns:
            OrderInfo (status=CANCELLED 또는 UNKNOWN)

        Raises:
            ValueError:    입력값 형식 오류
            KisApiError:   취소 실패 (이미 체결됨 등)
        """
        raise NotImplementedError

    @abstractmethod
    def get_order_status(
        self,
        order_no: str | None = None,
        *,
        filled_only: bool = False,
    ) -> "list[OrderInfo]":
        """
        주문 상태 조회.

        Args:
            order_no:    특정 주문번호만 필터 (None이면 전체)
            filled_only: True=당일 체결 내역, False=미체결 목록

        Returns:
            OrderInfo 리스트 (없으면 빈 리스트)
        """
        raise NotImplementedError

    def close(self) -> None:
        pass