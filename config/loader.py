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
    request_retry_count: int       # GET 재시도 횟수 (POST는 재시도 없음)
    request_retry_delay: int       # GET 재시도 기본 간격 (초)

    # KIS 레이트리밋 (모드별)
    kis_rate_limit_interval: float  # 호출 사이 최소 간격 (초)

    # HTTP
    http_user_agent: str

    # DB
    db_path: str = "./data/trading.db"
    db_busy_timeout_ms: int = 5000


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
    request_retry_count = int(
        _get_nested(yaml_data, "network", "request_retry_count")
    )
    request_retry_delay = int(
        _get_nested(yaml_data, "network", "request_retry_delay")
    )

    # 9-1. 네트워크 값 검증
    if request_timeout <= 0:
        raise SettingsError(
            f"network.request_timeout는 양수여야 합니다: {request_timeout}"
        )
    if token_retry_count < 1:
        raise SettingsError(
            f"network.token_retry_count는 1 이상이어야 합니다: "
            f"{token_retry_count}"
        )
    if token_retry_delay < 0:
        raise SettingsError(
            f"network.token_retry_delay는 0 이상이어야 합니다: "
            f"{token_retry_delay}"
        )
    if request_retry_count < 1:
        raise SettingsError(
            f"network.request_retry_count는 1 이상이어야 합니다: "
            f"{request_retry_count}"
        )
    if request_retry_delay < 0:
        raise SettingsError(
            f"network.request_retry_delay는 0 이상이어야 합니다: "
            f"{request_retry_delay}"
        )

    # 10. KIS 레이트리밋 (mode별)
    rate_limit_interval = float(
        _get_nested(yaml_data, "kis", mode, "rate_limit_interval")
    )
    if rate_limit_interval < 0:
        raise SettingsError(
            f"kis.{mode}.rate_limit_interval는 0 이상이어야 합니다: "
            f"{rate_limit_interval}"
        )

    # 11. HTTP
    http_user_agent = str(
        _get_nested(yaml_data, "http", "user_agent")
    ).strip()
    if not http_user_agent:
        raise SettingsError("http.user_agent는 비어있을 수 없습니다.")

    # 12. DB (env override 우선, 없으면 yaml, 둘 다 없으면 기본값)
    env_db_path = os.getenv("DB_PATH", "").strip()
    if env_db_path:
        db_path = env_db_path
    else:
        db_section = yaml_data.get("db", {})
        if not isinstance(db_section, dict):
            raise SettingsError("settings.yaml의 'db' 섹션이 dict가 아닙니다.")
        db_path = str(db_section.get("path", "./data/trading.db")).strip()
    if not db_path:
        raise SettingsError("db.path가 비어있습니다.")

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
        request_retry_count=request_retry_count,
        request_retry_delay=request_retry_delay,
        kis_rate_limit_interval=rate_limit_interval,
        http_user_agent=http_user_agent,
        db_path=db_path,
    )


def _is_valid_account_no(account_no: str) -> bool:
    """계좌번호 형식: 8자리-2자리."""
    parts = account_no.split("-")
    if len(parts) != 2:
        return False
    if len(parts[0]) != 8 or len(parts[1]) != 2:
        return False
    return parts[0].isdigit() and parts[1].isdigit()


# Canonical implementation below overrides the earlier legacy definitions.
class SettingsError(Exception):
    """Raised when configuration loading or validation fails."""


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    mode: str

    kis_app_key: str
    kis_app_secret: str
    kis_account_no: str

    kis_rest_url: str
    kis_ws_url: str

    token_expiry_buffer_minutes: int
    token_cache_dir: Path

    log_level: str
    log_dir: Path
    log_console: bool
    log_file: bool

    request_timeout: int
    token_retry_count: int
    token_retry_delay: int
    request_retry_count: int
    request_retry_delay: int

    kis_rate_limit_interval: float
    http_user_agent: str

    db_path: str = "./data/trading.db"
    db_busy_timeout_ms: int = 5000


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and validate the YAML settings file."""
    if not path.exists():
        raise SettingsError(f"Config file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise SettingsError(f"Failed to parse YAML: {path} - {exc}") from exc

    if not isinstance(data, dict):
        raise SettingsError(f"YAML root must be a dict: {path}")

    return data


def _require_env(key: str) -> str:
    """Read a required environment variable."""
    value = os.getenv(key, "").strip()
    if not value:
        raise SettingsError(
            f"Environment variable '{key}' is empty. Please check `.env`."
        )
    return value


def _get_nested(data: dict[str, Any], *keys: str) -> Any:
    """Safely read nested YAML keys."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            dotted_path = ".".join(keys)
            raise SettingsError(f"Missing settings.yaml key: '{dotted_path}'")
        current = current[key]
    return current


def _parse_positive_int(value: object, *, field_name: str) -> int:
    """Parse an integer that must be >= 1."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{field_name} must be an integer: {value!r}") from exc

    if parsed <= 0:
        raise SettingsError(f"{field_name} must be >= 1: {parsed}")
    return parsed


def _parse_non_negative_int(value: object, *, field_name: str) -> int:
    """Parse an integer that must be >= 0."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{field_name} must be an integer: {value!r}") from exc

    if parsed < 0:
        raise SettingsError(f"{field_name} must be >= 0: {parsed}")
    return parsed


def _is_valid_account_no(account_no: str) -> bool:
    """Validate KIS account number format: 8 digits, dash, 2 digits."""
    parts = account_no.split("-")
    if len(parts) != 2:
        return False
    if len(parts[0]) != 8 or len(parts[1]) != 2:
        return False
    return parts[0].isdigit() and parts[1].isdigit()


def load_settings() -> Settings:
    """Load settings from `.env` and `config/settings.yaml`."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise SettingsError(
            f".env file not found: {env_path}\n"
            "Copy `.env.example` to `.env` and fill in the KIS credentials."
        )
    load_dotenv(env_path)

    yaml_path = PROJECT_ROOT / "config" / "settings.yaml"
    yaml_data = _load_yaml(yaml_path)

    mode = _get_nested(yaml_data, "mode")
    if mode not in ("mock", "real"):
        raise SettingsError(f"mode must be 'mock' or 'real'. Current value: {mode!r}")

    if mode == "mock":
        app_key = _require_env("KIS_MOCK_APP_KEY")
        app_secret = _require_env("KIS_MOCK_APP_SECRET")
        account_no = _require_env("KIS_MOCK_ACCOUNT_NO")
    else:
        raise SettingsError(
            "real mode is not supported yet. It will be enabled only after the "
            "dedicated safety phase is completed."
        )

    if not _is_valid_account_no(account_no):
        raise SettingsError(
            f"Invalid KIS account number format: {account_no!r} "
            "(expected '12345678-01')"
        )

    kis_rest_url = _get_nested(yaml_data, "kis", mode, "rest_url")
    kis_ws_url = _get_nested(yaml_data, "kis", mode, "ws_url")

    token_expiry_buffer = _parse_positive_int(
        _get_nested(yaml_data, "token", "expiry_buffer_minutes"),
        field_name="token.expiry_buffer_minutes",
    )
    token_cache_dir = PROJECT_ROOT / str(_get_nested(yaml_data, "token", "cache_dir"))
    token_cache_dir.mkdir(parents=True, exist_ok=True)

    log_level = str(_get_nested(yaml_data, "logging", "level")).upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise SettingsError(f"Invalid logging.level: {log_level!r}")
    log_dir = PROJECT_ROOT / str(_get_nested(yaml_data, "logging", "log_dir"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_console = bool(_get_nested(yaml_data, "logging", "console"))
    log_file = bool(_get_nested(yaml_data, "logging", "file"))

    request_timeout = _parse_positive_int(
        _get_nested(yaml_data, "network", "request_timeout"),
        field_name="network.request_timeout",
    )
    token_retry_count = _parse_positive_int(
        _get_nested(yaml_data, "network", "token_retry_count"),
        field_name="network.token_retry_count",
    )
    token_retry_delay = _parse_non_negative_int(
        _get_nested(yaml_data, "network", "token_retry_delay"),
        field_name="network.token_retry_delay",
    )
    request_retry_count = _parse_positive_int(
        _get_nested(yaml_data, "network", "request_retry_count"),
        field_name="network.request_retry_count",
    )
    request_retry_delay = _parse_non_negative_int(
        _get_nested(yaml_data, "network", "request_retry_delay"),
        field_name="network.request_retry_delay",
    )

    rate_limit_interval = float(_get_nested(yaml_data, "kis", mode, "rate_limit_interval"))
    if rate_limit_interval < 0:
        raise SettingsError(
            f"kis.{mode}.rate_limit_interval must be >= 0: {rate_limit_interval}"
        )

    http_user_agent = str(_get_nested(yaml_data, "http", "user_agent")).strip()
    if not http_user_agent:
        raise SettingsError("http.user_agent cannot be empty.")

    db_section = yaml_data.get("db", {})
    if not isinstance(db_section, dict):
        raise SettingsError("settings.yaml 'db' section must be a dict.")

    env_db_path = os.getenv("DB_PATH", "").strip()
    db_path = env_db_path or str(db_section.get("path", "./data/trading.db")).strip()
    if not db_path:
        raise SettingsError("db.path cannot be empty.")

    env_db_busy_timeout = os.getenv("DB_BUSY_TIMEOUT_MS", "").strip()
    db_busy_timeout_raw: object = (
        env_db_busy_timeout
        if env_db_busy_timeout
        else db_section.get("busy_timeout_ms", 5000)
    )
    db_busy_timeout_ms = _parse_positive_int(
        db_busy_timeout_raw,
        field_name="db.busy_timeout_ms",
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
        request_retry_count=request_retry_count,
        request_retry_delay=request_retry_delay,
        kis_rate_limit_interval=rate_limit_interval,
        http_user_agent=http_user_agent,
        db_path=db_path,
        db_busy_timeout_ms=db_busy_timeout_ms,
    )


__all__ = [
    "PROJECT_ROOT",
    "Settings",
    "SettingsError",
    "load_settings",
]
