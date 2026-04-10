"""
브로커 추상 인터페이스.

향후 KIS 외 다른 증권사(키움, 넥스트레이드 등) 확장 시
이 인터페이스를 구현하면 된다.

Phase 0에서는 자리만 잡아둔다. Phase 1에서 메서드 추가.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BrokerInterface(ABC):
    """
    모든 브로커 구현체가 따라야 할 인터페이스.

    Phase 1에서 다음 메서드들이 추가될 예정:
        - get_balance()
        - get_current_price(code)
        - get_daily_candles(code, count)
        - get_minute_candles(code, interval, count)
        - place_order(...)
        - cancel_order(...)
    """

    @abstractmethod
    def get_access_token(self) -> str:
        """유효한 access token 반환. 없거나 만료 시 갱신."""
        raise NotImplementedError