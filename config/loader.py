"""
설정 로더.

- .env 파일에서 민감정보(앱키) 로드
- settings.yaml에서 일반 설정 로드
- 필수값 검증 후 단일 Settings 객체로 반환
- 검증 실패 시 프로그램 시작 시점에 즉시 예외 발생

사용 예:
    from config.loader import load_settings
    settings = load_settings()
    print(settings.mode)  # "mock"
    print(settings.kis_app_key)  # "..."
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


# 프로젝트 루트 (config/loader.py 기준 상위 폴더)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SettingsError(Exception):
    """설정 로드/검증 실패."""


@dataclass(frozen=True)
class Settings:
    """
    전역 설정 객체.

    frozen=True: 런타임 중 설정 변경 금지 (자동매매 안정성).
    변경이 필요하면 프로그램 재시작.
    """

    # 모드
    mode: str  # "mock" or "real"

    # KIS 앱키 (현재 모드에 해당하는 값만 로드)
    kis_app_key: str
    kis_app_secret: str
    kis_account_no: str

    # KIS 엔드포인트
    kis_rest_url: str
    kis_ws_url: str

    # 토큰
    token_expiry_buffer_minutes: int
    token_cache_dir: Path

    # 로깅
    log_level: str
    log_dir: Path
    log_console: bool
    log_file: bool

    # 네트워크
    request_timeout: int
    token_retry_count: int
    token_retry_delay: int


def _load_yaml(path: Path) -> dict[str, Any]:
    """YAML 파일 로드. 파일 없거나 파싱 실패 시 SettingsError."""
    if not path.exists():
        raise SettingsError(f"설정 파일을 찾을 수 없습니다: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise SettingsError(f"YAML 파싱 실패: {path} - {e}") from e
    if not isinstance(data, dict):
        raise SettingsError(f"YAML 루트가 dict가 아닙니다: {path}")
    return data


def _require_env(key: str) -> str:
    """환경변수 필수값 로드. 없거나 비어있으면 SettingsError."""
    value = os.getenv(key, "").strip()
    if not value:
        raise SettingsError(
            f"환경변수 '{key}'가 비어있습니다. .env 파일을 확인하세요."
        )
    return value


def _get_nested(data: dict[str, Any], *keys: str) -> Any:
    """중첩 dict에서 값 꺼내기. 없으면 SettingsError."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            path = ".".join(keys)
            raise SettingsError(f"settings.yaml에 '{path}' 항목이 없습니다.")
        current = current[key]
    return current


def load_settings() -> Settings:
    """
    설정 로드 및 검증.

    Returns:
        Settings: 검증 완료된 설정 객체

    Raises:
        SettingsError: 파일 없음, 필수값 누락, 타입 오류 등
    """
    # 1. .env 로드
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise SettingsError(
            f".env 파일을 찾을 수 없습니다: {env_path}\n"
            ".env.example을 복사해서 .env를 만들고 앱키를 입력하세요."
        )
    load_dotenv(env_path)

    # 2. settings.yaml 로드
    yaml_path = PROJECT_ROOT / "config" / "settings.yaml"
    yaml_data = _load_yaml(yaml_path)

    # 3. 모드 확인
    mode = _get_nested(yaml_data, "mode")
    if mode not in ("mock", "real"):
        raise SettingsError(
            f"mode는 'mock' 또는 'real'이어야 합니다. 현재: {mode!r}"
        )

    # 4. 모드에 해당하는 앱키 로드
    if mode == "mock":
        app_key = _require_env("KIS_MOCK_APP_KEY")
        app_secret = _require_env("KIS_MOCK_APP_SECRET")
        account_no = _require_env("KIS_MOCK_ACCOUNT_NO")
    else:
        # 실전 모드는 Phase 11 이후 활성화
        raise SettingsError(
            "실전 모드(real)는 아직 지원되지 않습니다. "
            "Phase 11 이후 안전장치가 갖춰진 후 활성화됩니다."
        )

    # 5. 계좌번호 형식 검증 (8자리-2자리)
    if not _is_valid_account_no(account_no):
        raise SettingsError(
            f"계좌번호 형식이 잘못되었습니다: {account_no!r} "
            "(예: '50123456-01')"
        )

    # 6. KIS 엔드포인트
    kis_rest_url = _get_nested(yaml_data, "kis", mode, "rest_url")
    kis_ws_url = _get_nested(yaml_data, "kis", mode, "ws_url")

    # 7. 토큰
    token_expiry_buffer = int(
        _get_nested(yaml_data, "token", "expiry_buffer_minutes")
    )
    token_cache_dir = PROJECT_ROOT / _get_nested(
        yaml_data, "token", "cache_dir"
    )
    token_cache_dir.mkdir(parents=True, exist_ok=True)

    # 8. 로깅
    log_level = str(_get_nested(yaml_data, "logging", "level")).upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise SettingsError(f"log level 값이 잘못되었습니다: {log_level}")
    log_dir = PROJECT_ROOT / _get_nested(yaml_data, "logging", "log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_console = bool(_get_nested(yaml_data, "logging", "console"))
    log_file = bool(_get_nested(yaml_data, "logging", "file"))

    # 9. 네트워크
    request_timeout = int(_get_nested(yaml_data, "network", "request_timeout"))
    token_retry_count = int(
        _get_nested(yaml_data, "network", "token_retry_count")
    )
    token_retry_delay = int(
        _get_nested(yaml_data, "network", "token_retry_delay")
    )

    return Settings(
        mode=mode,
        kis_app_key=app_key,
        kis_app_secret=app_secret,
        kis_account_no=account_no,
        kis_rest_url=kis_rest_url,
        kis_ws_url=kis_ws_url,
        token_expiry_buffer_minutes=token_expiry_buffer,
        token_cache_dir=token_cache_dir,
        log_level=log_level,
        log_dir=log_dir,
        log_console=log_console,
        log_file=log_file,
        request_timeout=request_timeout,
        token_retry_count=token_retry_count,
        token_retry_delay=token_retry_delay,
    )


def _is_valid_account_no(account_no: str) -> bool:
    """계좌번호 형식: 8자리-2자리."""
    parts = account_no.split("-")
    if len(parts) != 2:
        return False
    if len(parts[0]) != 8 or len(parts[1]) != 2:
        return False
    return parts[0].isdigit() and parts[1].isdigit()