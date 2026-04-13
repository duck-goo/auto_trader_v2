"""
Configuration loader.

- Sensitive values come from `.env`
- General settings come from `config/settings.yaml`
- Validation runs before the application starts
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    value = os.getenv(key, "").strip()
    if not value:
        raise SettingsError(
            f"Environment variable '{key}' is empty. Please check `.env`."
        )
    return value


def _get_nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            dotted_path = ".".join(keys)
            raise SettingsError(f"Missing settings.yaml key: '{dotted_path}'")
        current = current[key]
    return current


def _parse_positive_int(value: object, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{field_name} must be an integer: {value!r}") from exc

    if parsed <= 0:
        raise SettingsError(f"{field_name} must be >= 1: {parsed}")
    return parsed


def _parse_non_negative_int(value: object, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{field_name} must be an integer: {value!r}") from exc

    if parsed < 0:
        raise SettingsError(f"{field_name} must be >= 0: {parsed}")
    return parsed


def load_settings() -> Settings:
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


def _is_valid_account_no(account_no: str) -> bool:
    parts = account_no.split("-")
    if len(parts) != 2:
        return False
    if len(parts[0]) != 8 or len(parts[1]) != 2:
        return False
    return parts[0].isdigit() and parts[1].isdigit()


# Backward-compatibility shim. Canonical implementation now lives in config.loader.
from config.loader import (  # noqa: E402,F401
    PROJECT_ROOT,
    Settings,
    SettingsError,
    _is_valid_account_no,
    load_settings,
)
