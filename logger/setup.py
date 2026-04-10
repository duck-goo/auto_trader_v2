"""
로거 설정.

카테고리별로 별도 파일에 기록한다.
카테고리: system / login / scan / order / fill / error

사용 예:
    from logger import setup_logging, get_logger

    # 프로그램 시작 시 1회
    setup_logging(settings)

    # 각 모듈에서
    log = get_logger("system")
    log.info("프로그램 시작")

    log = get_logger("error")
    log.error("주문 실패", exc_info=True)
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging import Logger
from pathlib import Path

from config.loader import Settings


# 지원하는 카테고리
CATEGORIES = ("system", "login", "scan", "order", "fill", "error")

# 로그 포맷
_LOG_FORMAT = (
    "%(asctime)s [%(levelname)-7s] [%(name)s] "
    "%(filename)s:%(lineno)d - %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 중복 초기화 방지 플래그
_initialized = False


def setup_logging(settings: Settings) -> None:
    """
    로깅 시스템 초기화.

    프로그램 시작 시 1회만 호출한다.
    여러 번 호출되면 두 번째 이후는 무시된다.

    Args:
        settings: load_settings() 결과
    """
    global _initialized
    if _initialized:
        return

    # 오늘 날짜 폴더 생성
    today = datetime.now().strftime("%Y-%m-%d")
    date_dir = settings.log_dir / today
    date_dir.mkdir(parents=True, exist_ok=True)

    # 레벨 변환
    level = getattr(logging, settings.log_level, logging.INFO)

    # 루트 로거는 건드리지 않음.
    # 우리가 명시적으로 만든 카테고리 로거만 설정.
    for category in CATEGORIES:
        logger = logging.getLogger(f"grasshopper.{category}")
        logger.setLevel(level)
        logger.propagate = False  # 루트로 전파 방지 (중복 출력 방지)

        # 기존 핸들러 제거 (재초기화 안전장치)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

        # 파일 핸들러
        if settings.log_file:
            log_path = date_dir / f"{category}.log"
            file_handler = logging.FileHandler(
                log_path, mode="a", encoding="utf-8"
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        # 콘솔 핸들러 (에러는 stderr, 나머지는 stdout)
        if settings.log_console:
            if category == "error":
                console_handler = logging.StreamHandler(sys.stderr)
            else:
                console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(level)
            console_handler.setFormatter(formatter)
            # Windows 콘솔 한글 깨짐 방지
            _force_utf8_stream(console_handler)
            logger.addHandler(console_handler)

    _initialized = True

    # 초기화 완료 로그
    system_log = get_logger("system")
    system_log.info("=" * 60)
    system_log.info("로거 초기화 완료")
    system_log.info(f"모드: {settings.mode}")
    system_log.info(f"로그 레벨: {settings.log_level}")
    system_log.info(f"로그 경로: {date_dir}")
    system_log.info("=" * 60)


def get_logger(category: str) -> Logger:
    """
    카테고리 로거 반환.

    Args:
        category: system / login / scan / order / fill / error

    Returns:
        Logger: 해당 카테고리 로거

    Raises:
        ValueError: 지원하지 않는 카테고리
    """
    if category not in CATEGORIES:
        raise ValueError(
            f"지원하지 않는 로그 카테고리: {category!r}. "
            f"지원: {CATEGORIES}"
        )
    return logging.getLogger(f"grasshopper.{category}")


def _force_utf8_stream(handler: logging.StreamHandler) -> None:
    """
    Windows 콘솔(cp949)에서 한글 깨짐 방지.

    Python 3.9+ reconfigure() 사용.
    실패해도 프로그램은 계속 동작해야 하므로 예외는 삼킴.
    """
    stream = handler.stream
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # 일부 환경(파이프/리다이렉트)에서는 reconfigure 불가.
        # 이 경우 OS 기본 인코딩 사용.
        pass