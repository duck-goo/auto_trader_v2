"""
KIS 계좌 조회 모듈.

잔고/보유종목 조회를 담당한다.
KisClient를 주입받아 사용한다.
"""

from __future__ import annotations

from broker.kis.client import KisClient
from broker.kis.endpoints import (
    PATH_INQUIRE_BALANCE,
    TR_ID_INQUIRE_BALANCE,
)
from broker.kis.models import Balance
from broker.kis.parsers import parse_balance
from config.loader import Settings
from logger import get_logger


def _mask_account_no(account_no: str) -> str:
    if not isinstance(account_no, str):
        return "***"
    parts = account_no.split("-", 1)
    if len(parts) != 2 or len(parts[0]) < 4:
        return "***"
    return f"{parts[0][:4]}****-**"


class Account:
    """
    계좌 조회.

    잔고 조회는 계좌번호가 필요하므로 Settings를 주입받는다.
    """

    def __init__(self, client: KisClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._log = get_logger("system")

        # 계좌번호 분리 "12345678-01" → ("12345678", "01")
        parts = settings.kis_account_no.split("-")
        if len(parts) != 2:
            raise ValueError(
                f"계좌번호 형식 오류: {settings.kis_account_no}"
            )
        self._cano = parts[0]
        self._acnt_prdt_cd = parts[1]

    def get_balance(self) -> Balance:
        """
        주식 잔고 조회.

        Phase 1-A 제약:
            - 1페이지만 처리 (최대 약 50종목)
            - 페이징 필요 시 has_more_pages=True 로 표시
            - 추후 Phase에서 페이징 처리 추가

        Returns:
            Balance: 예수금 + 보유종목 리스트

        Raises:
            KisApiError: KIS 응답 실패
            KisParseError: 파싱 실패
        """
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        self._log.debug(
            f"잔고 조회 요청: {_mask_account_no(self._settings.kis_account_no)}"
        )
        response = self._client.request_get(
            path=PATH_INQUIRE_BALANCE,
            tr_id=TR_ID_INQUIRE_BALANCE,
            params=params,
        )

        balance = parse_balance(response)

        self._log.info(
            f"잔고 조회: 예수금={balance.cash:,}원, "
            f"주문가능={balance.available_cash:,}원, "
            f"평가액={balance.total_eval:,}원, "
            f"손익={balance.total_profit:+,}원, "
            f"보유종목={balance.holding_count}개"
            + (" (페이징 더 있음)" if balance.has_more_pages else "")
        )

        if balance.has_more_pages:
            self._log.warning(
                "잔고 조회: 다음 페이지가 있지만 Phase 1-A는 1페이지만 처리. "
                "보유종목이 많으면 일부가 누락됨. "
                "TODO: Phase 2 이후 페이징 구현."
            )

        return balance
