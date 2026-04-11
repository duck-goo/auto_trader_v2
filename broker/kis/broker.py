"""
KIS 브로커 Facade.

Auth/Client/Quote/Account를 내부에 소유하고 BrokerInterface로
외부에 노출한다. 외부 코드는 이 클래스 하나만 import하면 된다.

사용 예:
    from config.loader import load_settings
    from logger import setup_logging
    from broker.kis import KisBroker

    settings = load_settings()
    setup_logging(settings)

    with KisBroker(settings) as broker:
        snapshot = broker.get_current_price("005930")
        balance = broker.get_balance()
        daily = broker.get_daily_candles("005930", count=60)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from broker.base import BrokerInterface
from broker.kis.account import Account
from broker.kis.auth import KisAuth
from broker.kis.client import KisClient
from broker.kis.quote import Quote
from config.loader import Settings
from logger import get_logger

if TYPE_CHECKING:
    import pandas as pd
    from broker.kis.models import Balance, PriceSnapshot


class KisBroker(BrokerInterface):
    """
    KIS 브로커 Facade.

    내부에 Auth/Client/Quote/Account를 모두 소유한다.
    단일 인스턴스로 사용하는 것이 원칙 (레이트리밋 락 공유).

    라이프사이클:
        broker = KisBroker(settings)
        try:
            ...
        finally:
            broker.close()

        # 또는 context manager
        with KisBroker(settings) as broker:
            ...
    """

    def __init__(self, settings: Settings) -> None:
        """
        Args:
            settings: load_settings() 결과
        """
        self._settings = settings
        self._log = get_logger("system")

        # 내부 컴포넌트 조립
        self._auth = KisAuth(settings)
        self._client = KisClient(settings, self._auth)
        self._quote = Quote(self._client)
        self._account = Account(self._client, settings)

        self._log.info(
            f"KisBroker 초기화 완료 (mode={settings.mode}, "
            f"account={settings.kis_account_no})"
        )

    # ============================================================
    # 라이프사이클
    # ============================================================

    def close(self) -> None:
        """리소스 정리."""
        self._client.close()
        self._log.info("KisBroker 종료")

    def __enter__(self) -> "KisBroker":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ============================================================
    # BrokerInterface 구현
    # ============================================================

    def get_access_token(self) -> str:
        """Phase 0 호환. 내부 Auth에 위임."""
        return self._auth.get_access_token()

    def get_current_price(self, code: str) -> "PriceSnapshot":
        """현재가 조회."""
        return self._quote.get_current_price(code)

    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> "pd.DataFrame":
        """일봉 조회."""
        return self._quote.get_daily_candles(code, count, end_date)

    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> "pd.DataFrame":
        """분봉 조회."""
        return self._quote.get_minute_candles(code, interval)

    def get_balance(self) -> "Balance":
        """잔고 조회."""
        return self._account.get_balance()

    # ============================================================
    # 고급 사용자용 (내부 컴포넌트 직접 접근)
    # ============================================================

    @property
    def auth(self) -> KisAuth:
        """내부 KisAuth 인스턴스 (일반 사용 시 불필요)."""
        return self._auth

    @property
    def client(self) -> KisClient:
        """내부 KisClient 인스턴스 (일반 사용 시 불필요)."""
        return self._client