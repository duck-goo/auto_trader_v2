"""
KIS API 엔드포인트 경로 및 TR ID 상수.

실제 BASE URL은 settings.yaml에서 모드별로 관리.
여기는 경로와 TR ID만 상수화.

Phase 1에서 시세/잔고/주문 TR ID가 추가될 예정.
"""

from __future__ import annotations


# ============================================
# 인증 (Phase 0)
# ============================================

# 접근토큰 발급 경로
PATH_TOKEN_ISSUE = "/oauth2/tokenP"

# 접근토큰 폐기 경로 (강제 재발급 시 사용)
PATH_TOKEN_REVOKE = "/oauth2/revokeP"


# ============================================
# 시세 (Phase 1에서 추가 예정)
# ============================================
# PATH_INQUIRE_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
# TR_ID_INQUIRE_PRICE = "FHKST01010100"

# ============================================
# 시세 (Phase 1-A에서 사용)
# ============================================

# 주식 현재가 시세
PATH_INQUIRE_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
TR_ID_INQUIRE_PRICE = "FHKST01010100"

# 일봉 (inquire-daily-itemchartprice)
PATH_INQUIRE_DAILY = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
TR_ID_INQUIRE_DAILY = "FHKST03010100"

# 분봉 (inquire-time-itemchartprice) - 1분봉만 지원
PATH_INQUIRE_MINUTE = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
TR_ID_INQUIRE_MINUTE = "FHKST03010200"

# ============================================
# 계좌 (Phase 1-A)
# ============================================

# 주식 잔고 조회
PATH_INQUIRE_BALANCE = "/uapi/domestic-stock/v1/trading/inquire-balance"
TR_ID_INQUIRE_BALANCE = "TTTC8434R"  # 모의는 자동 VTTC8434R 변환