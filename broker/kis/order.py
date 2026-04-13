"""
KIS 주문 모듈.

책임:
    - place_order  : 매수/매도 주문 전송
    - cancel_order : 주문 전량 취소
    - get_order_status : 미체결 / 당일 체결 조회

설계 원칙:
    - POST 절대 재시도 없음 (자금 이동 안전성)
    - 중복 주문 방지: code+side 키로 진행 중 주문 추적
    - POST 네트워크 실패 → UNKNOWN 상태 보존, KisOrderError 전파
    - 취소는 중복 방지 대상 아님 (취소 자체는 안전)
    - 계좌번호는 settings에서 분리 (CANO 8자리 / ACNT_PRDT_CD 2자리)
"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Any

import pytz

from broker.kis.client import KisClient
from broker.kis.endpoints import (
    PATH_INQUIRE_DAILY_CCLD,
    PATH_INQUIRE_PSBL_RVSECNCL,
    PATH_ORDER_CASH,
    PATH_ORDER_RVSECNCL,
    TR_ID_BUY,
    TR_ID_CANCEL,
    TR_ID_INQUIRE_DAILY_CCLD,
    TR_ID_INQUIRE_PSBL_RVSECNCL,
    TR_ID_SELL,
)
from broker.kis.errors import KisApiError, KisOrderError
from broker.kis.models import (
    OrderInfo,
    OrderSide,
    OrderStatus,
    OrderType,
)
from broker.kis.parsers import (
    parse_cancel_response,
    parse_filled_order_list,
    parse_order_response,
    parse_pending_order_list,
)
from config.loader import Settings
from logger import get_logger

KST = pytz.timezone("Asia/Seoul")

# 종목코드 형식 (6자리 숫자)
_CODE_PATTERN = re.compile(r"^\d{6}$")

# 계좌번호 형식 ("12345678-01")
_ACCOUNT_PATTERN = re.compile(r"^(\d{8})-(\d{2})$")


# ============================================================
# 유틸 함수 (모듈 내부 전용)
# ============================================================

def _split_account_no(account_no: str) -> tuple[str, str]:
    """
    "50123456-01" → ("50123456", "01").

    Raises:
        ValueError: 형식 불일치
    """
    m = _ACCOUNT_PATTERN.match(account_no)
    if not m:
        raise ValueError(
            f"계좌번호 형식 오류: {account_no!r} (기대: '12345678-01')"
        )
    return m.group(1), m.group(2)


def _validate_order_inputs(
    code: str,
    side: str,
    quantity: int,
    price: int,
) -> tuple[OrderSide, OrderType]:
    """
    주문 입력값 검증.

    Returns:
        (OrderSide, OrderType) 변환 결과

    Raises:
        ValueError: 형식/범위 오류
    """
    if not isinstance(code, str) or not _CODE_PATTERN.match(code):
        raise ValueError(f"종목코드는 6자리 숫자여야 합니다: {code!r}")

    try:
        order_side = OrderSide(side)
    except ValueError:
        raise ValueError(f"side는 'buy' 또는 'sell'이어야 합니다: {side!r}")

    if not isinstance(quantity, int) or quantity <= 0:
        raise ValueError(f"quantity는 1 이상 정수여야 합니다: {quantity!r}")

    if not isinstance(price, int) or price < 0:
        raise ValueError(f"price는 0 이상 정수여야 합니다: {price!r}")

    order_type = OrderType.MARKET if price == 0 else OrderType.LIMIT
    return order_side, order_type


def _make_pending_key(code: str, side: str) -> str:
    """중복 방지용 키 생성. code+side 조합."""
    return f"{code}:{side}"


# ============================================================
# Order 클래스
# ============================================================

class Order:
    """
    KIS 주문 실행 모듈.

    KisClient를 주입받아 주문/취소/조회를 담당한다.
    인스턴스는 KisBroker와 동일 수명주기로 1개만 생성한다.

    중복 방지:
        _pending_set에 "code:side" 키를 관리.
        POST 전송 직전 키 등록, 성공/실패 모두 finally에서 키 제거.
        같은 code+side 주문이 진행 중이면 KisOrderError 즉시 발생.
    """

    def __init__(self, client: KisClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._log = get_logger("order")
        self._error_log = get_logger("error")

        # 중복 방지
        self._pending_lock = threading.Lock()
        self._pending_set: set[str] = set()

        # 계좌번호 분리 (초기화 시점에 검증)
        self._cano, self._acnt_prdt_cd = _split_account_no(
            settings.kis_account_no
        )

        self._log.info(
            f"Order 초기화: account={settings.kis_account_no}, "
            f"mode={settings.mode}"
        )

    # ============================================================
    # Public API
    # ============================================================

    def place_order(
        self,
        code: str,
        side: str,
        quantity: int,
        price: int = 0,
    ) -> OrderInfo:
        """
        매수/매도 주문 전송.

        흐름:
            1. 입력값 검증
            2. 중복 방지 키 확인 → 이미 있으면 KisOrderError
            3. 키 등록 후 POST 전송
            4. 성공 → ACCEPTED OrderInfo 반환
            5. KIS 거부(rt_cd≠0) → REJECTED 로그 후 KisApiError 전파
            6. 네트워크 실패 → UNKNOWN 로그 후 KisOrderError 전파
            7. finally → 키 반드시 제거

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
        order_side, order_type = _validate_order_inputs(
            code, side, quantity, price
        )
        pending_key = _make_pending_key(code, side)
        timestamp = datetime.now(KST)

        # --- 중복 방지 ---
        with self._pending_lock:
            if pending_key in self._pending_set:
                raise KisOrderError(
                    f"중복 주문 차단: {code} {side} 주문이 이미 진행 중입니다."
                )
            self._pending_set.add(pending_key)

        self._log.info(
            f"주문 시도: code={code} side={side} qty={quantity} "
            f"price={price} type={order_type.value}"
        )

        try:
            body = self._build_order_body(
                code=code,
                order_type=order_type,
                quantity=quantity,
                price=price,
            )
            tr_id = TR_ID_BUY if order_side == OrderSide.BUY else TR_ID_SELL

            response = self._client.request_post(
                path=PATH_ORDER_CASH,
                tr_id=tr_id,
                body=body,
            )

            order_info = parse_order_response(
                response,
                code=code,
                side=order_side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                timestamp=timestamp,
            )
            self._log.info(
                f"주문 접수 완료: order_no={order_info.order_no} "
                f"code={code} side={side} qty={quantity} price={price}"
            )
            return order_info

        except KisApiError as e:
            # KIS가 명시적으로 거부한 경우 (잔량 부족, 거래시간 외 등)
            self._error_log.error(
                f"주문 거부(REJECTED): code={code} side={side} "
                f"qty={quantity} price={price} | {e}"
            )
            raise

        except Exception as e:
            # 네트워크 실패 등 → UNKNOWN
            unknown_info = OrderInfo(
                code=code,
                side=order_side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.UNKNOWN,
                order_no=None,
                filled_qty=0,
                timestamp=timestamp,
                raw_response={},
            )
            self._error_log.error(
                f"주문 UNKNOWN: code={code} side={side} "
                f"qty={quantity} price={price} | "
                f"{type(e).__name__}: {e} | "
                f"get_order_status()로 실제 접수 여부 확인 필요"
            )
            raise KisOrderError(
                f"POST 전송 결과 불확실 (UNKNOWN). "
                f"get_order_status()로 미체결 확인 후 판단하세요. "
                f"원인: {type(e).__name__}: {e}",
                order_info=unknown_info,
            ) from e

        finally:
            with self._pending_lock:
                self._pending_set.discard(pending_key)

    def cancel_order(
        self,
        order_no: str,
        code: str,
        quantity: int,
    ) -> OrderInfo:
        """
        주문 전량 취소.

        취소는 중복 방지 대상이 아님.
        (취소 재시도는 이미 취소된 주문에 대해 KIS가 에러를 돌려주므로
         자연스럽게 중복 실행이 방지됨)

        Args:
            order_no: KIS 주문번호 (place_order 반환값의 order_no)
            code:     6자리 종목코드
            quantity: 취소할 수량 (원주문 수량과 동일하게 전달)

        Returns:
            OrderInfo(status=CANCELLED)

        Raises:
            ValueError:  입력값 형식 오류
            KisApiError: 취소 실패 (이미 체결, 주문번호 없음 등)
            KisOrderError: POST 네트워크 실패(UNKNOWN)
        """
        if not isinstance(order_no, str) or not order_no.strip():
            raise ValueError(f"order_no가 비어있습니다: {order_no!r}")
        if not isinstance(code, str) or not _CODE_PATTERN.match(code):
            raise ValueError(f"종목코드 형식 오류: {code!r}")
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError(f"quantity는 1 이상이어야 합니다: {quantity!r}")

        timestamp = datetime.now(KST)
        self._log.info(
            f"취소 시도: order_no={order_no} code={code} qty={quantity}"
        )

        try:
            body = self._build_cancel_body(
                order_no=order_no,
                code=code,
                quantity=quantity,
            )
            response = self._client.request_post(
                path=PATH_ORDER_RVSECNCL,
                tr_id=TR_ID_CANCEL,
                body=body,
            )

            # 취소된 주문의 side/type은 응답에 없음 → BUY로 채움 (감사 로그용)
            # 실제 side가 필요하면 호출자가 OrderInfo를 보관해야 함
            cancel_info = parse_cancel_response(
                response,
                code=code,
                side=OrderSide.BUY,   # TODO: 호출자에서 전달하도록 개선 가능
                order_type=OrderType.LIMIT,
                quantity=quantity,
                price=0,
                timestamp=timestamp,
            )
            self._log.info(
                f"취소 완료: cancel_order_no={cancel_info.order_no} "
                f"orig_order_no={order_no} code={code}"
            )
            return cancel_info

        except KisApiError as e:
            self._error_log.error(
                f"취소 실패: order_no={order_no} code={code} | {e}"
            )
            raise

        except Exception as e:
            unknown_info = OrderInfo(
                code=code,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                price=0,
                status=OrderStatus.UNKNOWN,
                order_no=order_no,
                filled_qty=0,
                timestamp=timestamp,
                raw_response={},
            )
            self._error_log.error(
                f"취소 UNKNOWN: order_no={order_no} code={code} | "
                f"{type(e).__name__}: {e}"
            )
            raise KisOrderError(
                f"취소 POST 결과 불확실 (UNKNOWN). "
                f"get_order_status()로 확인 필요. "
                f"원인: {type(e).__name__}: {e}",
                order_info=unknown_info,
            ) from e

    def get_order_status(
        self,
        order_no: str | None = None,
        *,
        filled_only: bool = False,
    ) -> list[OrderInfo]:
        """
        주문 상태 조회.

        Args:
            order_no:    특정 주문번호 필터 (None이면 전체)
            filled_only: True=당일 체결 내역, False=미체결 목록

        Returns:
            OrderInfo 리스트 (없으면 빈 리스트)

        Raises:
            KisApiError:  KIS 응답 실패
            KisParseError: 응답 파싱 실패
        """
        if filled_only:
            orders = self._fetch_filled_orders()
        else:
            orders = self._fetch_pending_orders()

        if order_no is not None:
            orders = [o for o in orders if o.order_no == order_no]

        self._log.info(
            f"주문 조회: filled_only={filled_only} "
            f"order_no={order_no} 결과={len(orders)}건"
        )
        return orders

    # ============================================================
    # 내부: HTTP 요청
    # ============================================================

    def _fetch_pending_orders(self) -> list[OrderInfo]:
        """
        미체결 조회 GET.

        모의투자 주의:
            KIS 모의투자는 inquire-psbl-rvsecncl (VTTC8036R)을
            지원하지 않는다 (msg_cd=90000000).
            모의 모드에서는 빈 리스트를 반환하고 경고 로그를 남긴다.
            실전 모드에서는 정상 동작한다.
        """
        if self._settings.mode == "mock":
            self._log.warning(
                "미체결 조회(inquire-psbl-rvsecncl)는 모의투자 미지원. "
                "빈 리스트 반환. 실전 모드에서는 정상 동작함."
            )
            return []

        params = self._build_inquiry_params()
        response = self._client.request_get(
            path=PATH_INQUIRE_PSBL_RVSECNCL,
            tr_id=TR_ID_INQUIRE_PSBL_RVSECNCL,
            params=params,
        )
        return parse_pending_order_list(response)

    def _fetch_filled_orders(self) -> list[OrderInfo]:
        """당일 체결 조회 GET."""
        today = datetime.now(KST).strftime("%Y%m%d")
        params: dict[str, Any] = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",   # 전체 (01=매도, 02=매수)
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "0",         # "" → "0" (전체) 수정
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        response = self._client.request_get(
            path=PATH_INQUIRE_DAILY_CCLD,
            tr_id=TR_ID_INQUIRE_DAILY_CCLD,
            params=params,
        )
        return parse_filled_order_list(response)

    # ============================================================
    # 내부: 요청 Body 조립
    # ============================================================

    def _build_order_body(
        self,
        code: str,
        order_type: OrderType,
        quantity: int,
        price: int,
    ) -> dict[str, str]:
        """
        매수/매도 POST body 조립.

        ORD_DVSN: "00"=지정가, "01"=시장가
        시장가 주문 시 ORD_UNPR은 "0" 전달 (KIS 스펙).
        """
        ord_dvsn = "01" if order_type == OrderType.MARKET else "00"
        return {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }

    def _build_cancel_body(
        self,
        order_no: str,
        code: str,
        quantity: int,
    ) -> dict[str, str]:
        """
        취소 POST body 조립.

        RVSE_CNCL_DVSN_CD: "01"=정정, "02"=취소
        QTY_ALL_ORD_YN: "Y"=전량취소 (이번 Phase는 전량만 지원)
        KRX_FWDG_ORD_ORGNO: 공백 허용 (KIS 문서 확인)
        """
        return {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }

    def _build_inquiry_params(self) -> dict[str, str]:
        """미체결 조회 파라미터."""
        return {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "",
            "INQR_DVSN_2": "",
        }