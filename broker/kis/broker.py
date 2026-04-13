"""
KIS 브로커 Facade.

Auth/Client/Quote/Account/Order를 내부에 소유하고
BrokerInterface로 외부에 노출한다.
외부 코드는 이 클래스 하나만 import하면 된다.

사용 예:
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisBroker

    settings = load_settings()
    setup_logging(settings)

    with KisBroker(settings) as broker:
        snapshot = broker.get_current_price("005930")
        balance  = broker.get_balance()
        order    = broker.place_order("005930", "buy", 10, 70000)
        orders   = broker.get_order_status()
        broker.cancel_order(order.order_no, "005930", 10)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from broker.base import BrokerInterface
from broker.kis.account import Account
from broker.kis.auth import KisAuth
from broker.kis.client import KisClient
from broker.kis.order import Order
from broker.kis.quote import Quote
from config.loader import Settings
from logger import get_logger

if TYPE_CHECKING:
    import pandas as pd
    from broker.kis.models import Balance, OrderInfo, PriceSnapshot


class KisBroker(BrokerInterface):
    """
    KIS 브로커 Facade.

    내부에 Auth/Client/Quote/Account/Order를 모두 소유한다.
    단일 인스턴스로 사용하는 것이 원칙 (레이트리밋 락 공유).

    라이프사이클:
        with KisBroker(settings) as broker:
            ...
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = get_logger("system")

        self._auth    = KisAuth(settings)
        self._client  = KisClient(settings, self._auth)
        self._quote   = Quote(self._client)
        self._account = Account(self._client, settings)
        self._order   = Order(self._client, settings)

        self._log.info(
            f"KisBroker 초기화 완료 (mode={settings.mode}, "
            f"account={settings.kis_account_no})"
        )

    # ============================================================
    # 라이프사이클
    # ============================================================

    def close(self) -> None:
        self._client.close()
        self._log.info("KisBroker 종료")

    def __enter__(self) -> "KisBroker":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ============================================================
    # Phase 1-A: 조회
    # ============================================================

    def get_access_token(self) -> str:
        return self._auth.get_access_token()

    def get_current_price(self, code: str) -> "PriceSnapshot":
        return self._quote.get_current_price(code)

    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> "pd.DataFrame":
        return self._quote.get_daily_candles(code, count, end_date)

    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> "pd.DataFrame":
        return self._quote.get_minute_candles(code, interval)

    def get_balance(self) -> "Balance":
        return self._account.get_balance()

    # ============================================================
    # Phase 1-B: 주문
    # ============================================================

    def place_order(
        self,
        code: str,
        side: str,
        quantity: int,
        price: int = 0,
    ) -> "OrderInfo":
        """
        매수/매도 주문.

        Args:
            code:     6자리 종목코드
            side:     "buy" | "sell"
            quantity: 1 이상 정수
            price:    0=시장가, >0=지정가

        Returns:
            OrderInfo(status=ACCEPTED)

        Raises:
            ValueError:    입력값 형식 오류
            KisOrderError: 중복 주문 / POST 네트워크 실패(UNKNOWN)
            KisApiError:   KIS 거부(REJECTED)
        """
        return self._order.place_order(code, side, quantity, price)

    def cancel_order(
        self,
        order_no: str,
        code: str,
        quantity: int,
    ) -> "OrderInfo":
        """
        주문 전량 취소.

        Args:
            order_no: place_order 반환값의 order_no
            code:     6자리 종목코드
            quantity: 원주문 수량

        Returns:
            OrderInfo(status=CANCELLED)

        Raises:
            ValueError:    입력값 형식 오류
            KisApiError:   취소 실패 (이미 체결 등)
            KisOrderError: POST 네트워크 실패(UNKNOWN)
        """
        return self._order.cancel_order(order_no, code, quantity)

    def get_order_status(
        self,
        order_no: str | None = None,
        *,
        filled_only: bool = False,
    ) -> "list[OrderInfo]":
        """
        주문 상태 조회.

        Args:
            order_no:    특정 주문번호 필터 (None=전체)
            filled_only: True=당일 체결, False=미체결

        Returns:
            OrderInfo 리스트 (없으면 빈 리스트)
        """
        return self._order.get_order_status(order_no, filled_only=filled_only)

    # ============================================================
    # 고급 사용자용 (내부 컴포넌트 직접 접근)
    # ============================================================

    @property
    def auth(self) -> KisAuth:
        return self._auth

    @property
    def client(self) -> KisClient:
        return self._client