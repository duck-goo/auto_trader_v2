"""
Serve the dashboard React app and one local snapshot API.

Routes:
- / or /index.html: serve the built React app
- /api/health: simple health payload
- /api/dashboard-snapshot: build and return one dashboard snapshot JSON

Safety:
- read-only for ops artifacts
- serves files only from the configured app directory
- query overrides are limited to trade_date
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging
import scripts.build_dashboard_snapshot as dashboard_snapshot_script
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import TradingControlRepository
from strategy import BUY_STRATEGY_CHOICES, resolve_buy_strategy_selection

KST = pytz.timezone("Asia/Seoul")
TRADE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_JSON_BODY_BYTES = 4096
KILL_SWITCH_ENABLE_ARTIFACT = "kill_switch.enable.json"
KILL_SWITCH_DISABLE_ARTIFACT = "kill_switch.disable.json"
DEFAULT_ENABLE_NOTE = "dashboard emergency stop"
DEFAULT_DB_BUSY_TIMEOUT_MS = 5000
MAX_NOTE_LENGTH = 200


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the dashboard app and local snapshot API."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Default: 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind. Default: 8765",
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Default trade date YYYY-MM-DD for snapshot API.",
    )
    parser.add_argument(
        "--ops-dir",
        default=None,
        help="Optional ops directory override.",
    )
    parser.add_argument(
        "--daily-report-input",
        default=None,
        help="Optional daily_ops_report.json override.",
    )
    parser.add_argument(
        "--daily-check-input",
        default=None,
        help="Optional daily_ops_check.json override.",
    )
    parser.add_argument(
        "--rehearsal-input",
        default=None,
        help="Optional rehearsal_summary.json override.",
    )
    parser.add_argument(
        "--app-dir",
        default=str(PROJECT_ROOT / "ui" / "dashboard_app" / "dist"),
        help="Directory to serve static dashboard assets from.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override for Kill Switch writes.",
    )
    return parser.parse_args()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _validate_trade_date(trade_date: str) -> str:
    if not TRADE_DATE_PATTERN.fullmatch(trade_date):
        raise ValueError("trade_date must match YYYY-MM-DD")
    return trade_date


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _resolve_ops_dir_for_trade_date(
    *,
    config: "DashboardServerConfig",
    trade_date: str,
) -> Path:
    if config.ops_dir:
        return dashboard_snapshot_script._resolve_path(config.ops_dir)
    return (PROJECT_ROOT / "data" / "ops" / trade_date).resolve()


def _load_runtime_settings() -> Any:
    settings = load_settings()
    setup_logging(settings)
    return settings


def _resolve_db_config(config: "DashboardServerConfig") -> tuple[Path, int]:
    if config.db_path:
        return (
            dashboard_snapshot_script._resolve_path(config.db_path),
            DEFAULT_DB_BUSY_TIMEOUT_MS,
        )

    settings = _load_runtime_settings()
    return (
        dashboard_snapshot_script._resolve_path(str(settings.db_path)),
        settings.db_busy_timeout_ms,
    )


def _normalize_kill_switch_note(*, enabled: bool, note: Any) -> str | None:
    if note is None:
        if enabled:
            return DEFAULT_ENABLE_NOTE
        raise ValueError("note is required when disabling Kill Switch")
    if not isinstance(note, str):
        raise ValueError("note must be a string")

    normalized = note.strip()
    if not normalized and enabled:
        return DEFAULT_ENABLE_NOTE
    if not normalized:
        raise ValueError("note is required when disabling Kill Switch")
    if len(normalized) > MAX_NOTE_LENGTH:
        raise ValueError(f"note must be {MAX_NOTE_LENGTH} characters or fewer")
    return normalized


def _normalize_optional_note(note: Any) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise ValueError("note must be a string")
    normalized = note.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_NOTE_LENGTH:
        raise ValueError(f"note must be {MAX_NOTE_LENGTH} characters or fewer")
    return normalized


def _build_kill_switch_controls(
    *,
    enabled: bool,
    note: str | None,
    updated_at: str | None,
) -> dict[str, Any]:
    return {
        "kill_switch_enabled": enabled,
        "kill_switch_note": note,
        "kill_switch_updated_at": updated_at,
        "kill_switch_status_level": "CRITICAL" if enabled else "READY",
    }


def _build_buy_strategy_selection(
    *,
    buy_strategy: str,
    note: str | None,
    updated_at: str,
) -> dict[str, Any]:
    if buy_strategy not in BUY_STRATEGY_CHOICES:
        raise ValueError(
            "buy_strategy must be one of "
            f"{', '.join(BUY_STRATEGY_CHOICES)}: {buy_strategy!r}"
        )
    run_timing1, run_timing2 = resolve_buy_strategy_selection(
        buy_strategy=buy_strategy,
        scan_timing1=False,
        scan_timing2=False,
    )
    return {
        "action": "SET",
        "buy_strategy": buy_strategy,
        "effective_buy_strategy": buy_strategy,
        "run_timing1": run_timing1,
        "run_timing2": run_timing2,
        "updated_at": updated_at,
        "note": note,
        "applies_to_next_run": True,
    }


def _load_kill_switch_controls_from_db(
    config: "DashboardServerConfig",
) -> dict[str, Any] | None:
    try:
        db_path, _ = _resolve_db_config(config)
    except Exception:
        return None
    if not db_path.exists():
        return None

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            f"{db_path.as_uri()}?mode=ro",
            uri=True,
            timeout=1,
        )
        conn.row_factory = sqlite3.Row
        row = TradingControlRepository(conn).get_kill_switch()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            conn.close()

    if row is None:
        return None
    return _build_kill_switch_controls(
        enabled=row.is_enabled,
        note=row.note,
        updated_at=row.updated_at,
    )


def _client_is_loopback(client_address: tuple[str, int] | None) -> bool:
    if client_address is None:
        return False
    host = client_address[0]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class DashboardServerConfig:
    host: str
    port: int
    trade_date: str
    ops_dir: str | None
    daily_report_input: str | None
    daily_check_input: str | None
    rehearsal_input: str | None
    app_dir: Path
    db_path: str | None


class DashboardThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        config: DashboardServerConfig,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.config = config
        self.db_ready = False
        self.db_ready_lock = Lock()
        self.db_path: Path | None = None
        self.db_busy_timeout_ms: int | None = None


def _ensure_db_ready(
    server: DashboardThreadingHTTPServer,
) -> tuple[Path, int]:
    if (
        server.db_ready
        and server.db_path is not None
        and server.db_busy_timeout_ms is not None
    ):
        return server.db_path, server.db_busy_timeout_ms

    with server.db_ready_lock:
        if (
            server.db_ready
            and server.db_path is not None
            and server.db_busy_timeout_ms is not None
        ):
            return server.db_path, server.db_busy_timeout_ms

        db_path, busy_timeout_ms = _resolve_db_config(server.config)
        run_migrations(db_path)
        server.db_path = db_path
        server.db_busy_timeout_ms = busy_timeout_ms
        server.db_ready = True
        return db_path, busy_timeout_ms


class DashboardAppRequestHandler(BaseHTTPRequestHandler):
    server: DashboardThreadingHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "trade_date": self.server.config.trade_date,
                },
            )
            return

        if parsed.path == "/api/dashboard-snapshot":
            self._handle_dashboard_snapshot(parsed.query)
            return

        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/kill-switch":
            self._handle_kill_switch_update()
            return
        if parsed.path == "/api/buy-strategy":
            self._handle_buy_strategy_update()
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error_type": "NotFound",
                "error_message": "Unknown API route.",
            },
        )

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_dashboard_snapshot(self, query: str) -> None:
        try:
            params = parse_qs(query, keep_blank_values=False)
            requested_trade_date = _optional_text(
                params.get("trade_date", [self.server.config.trade_date])[0]
            )
            trade_date = _validate_trade_date(
                requested_trade_date or self.server.config.trade_date
            )
            payload, _, _, _ = dashboard_snapshot_script.build_dashboard_snapshot_document(
                trade_date=trade_date,
                ops_dir=self.server.config.ops_dir,
                daily_report_input=self.server.config.daily_report_input,
                daily_check_input=self.server.config.daily_check_input,
                rehearsal_input=self.server.config.rehearsal_input,
            )
            kill_switch_controls = _load_kill_switch_controls_from_db(
                self.server.config
            )
            if kill_switch_controls is not None:
                payload["controls"] = kill_switch_controls
        except Exception as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            return

        self._send_json(HTTPStatus.OK, payload)

    def _handle_buy_strategy_update(self) -> None:
        if not _client_is_loopback(self.client_address):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error_type": "ForbiddenClient",
                    "error_message": "Buy strategy updates are allowed only from loopback clients.",
                },
            )
            return

        try:
            payload = self._read_json_body()
            buy_strategy = _optional_text(payload.get("buy_strategy"))
            if buy_strategy not in BUY_STRATEGY_CHOICES:
                raise ValueError(
                    "buy_strategy must be one of "
                    f"{', '.join(BUY_STRATEGY_CHOICES)}"
                )
            requested_trade_date = _optional_text(
                payload.get("trade_date")
            ) or self.server.config.trade_date
            trade_date = _validate_trade_date(requested_trade_date)
            note = _normalize_optional_note(payload.get("note"))
            updated_at = datetime.now(KST).isoformat()
            strategy_payload = _build_buy_strategy_selection(
                buy_strategy=buy_strategy,
                note=note,
                updated_at=updated_at,
            )
            artifact_path = (
                _resolve_ops_dir_for_trade_date(
                    config=self.server.config,
                    trade_date=trade_date,
                )
                / dashboard_snapshot_script.BUY_STRATEGY_SELECTION_FILE
            )
            _save_json(artifact_path, strategy_payload)
        except Exception as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "trade_date": trade_date,
                "artifact_path": str(artifact_path),
                "strategy": {
                    "selection_available": True,
                    "source": "selection_artifact",
                    "selection_path": str(artifact_path),
                    **strategy_payload,
                    "status_level": "READY",
                },
            },
        )

    def _handle_kill_switch_update(self) -> None:
        if not _client_is_loopback(self.client_address):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error_type": "ForbiddenClient",
                    "error_message": "Kill Switch updates are allowed only from loopback clients.",
                },
            )
            return

        try:
            payload = self._read_json_body()
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            requested_trade_date = _optional_text(
                payload.get("trade_date")
            ) or self.server.config.trade_date
            trade_date = _validate_trade_date(requested_trade_date)
            note = _normalize_kill_switch_note(
                enabled=enabled,
                note=payload.get("note"),
            )
        except Exception as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            return

        try:
            db_path, busy_timeout_ms = _ensure_db_ready(self.server)
            conn = get_connection(
                db_path,
                busy_timeout_ms=busy_timeout_ms,
            )
            try:
                now_text = datetime.now(KST).isoformat()
                repo = TradingControlRepository(conn)
                with transaction(conn):
                    row = repo.set_kill_switch(
                        is_enabled=enabled,
                        updated_at=now_text,
                        note=note,
                    )
            finally:
                conn.close()

            action = "ENABLE" if row.is_enabled else "DISABLE"
            artifact_name = (
                KILL_SWITCH_ENABLE_ARTIFACT
                if row.is_enabled
                else KILL_SWITCH_DISABLE_ARTIFACT
            )
            artifact_path = (
                _resolve_ops_dir_for_trade_date(
                    config=self.server.config,
                    trade_date=trade_date,
                )
                / artifact_name
            )
            artifact_payload = {
                "action": action,
                "enabled": row.is_enabled,
                "note": row.note,
                "updated_at": row.updated_at,
            }
            _save_json(artifact_path, artifact_payload)
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "trade_date": trade_date,
                "action": action,
                "artifact_path": str(artifact_path),
                "controls": _build_kill_switch_controls(
                    enabled=row.is_enabled,
                    note=row.note,
                    updated_at=row.updated_at,
                ),
            },
        )

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length header is required")
        try:
            body_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length header must be an integer") from exc
        if body_length <= 0:
            return {}
        if body_length > MAX_JSON_BODY_BYTES:
            raise ValueError("JSON body is too large")

        raw_body = self.rfile.read(body_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _serve_static(self, request_path: str) -> None:
        app_dir = self.server.config.app_dir
        requested = request_path.lstrip("/")
        candidate_path = (app_dir / requested).resolve()

        try:
            candidate_path.relative_to(app_dir)
        except ValueError:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error_type": "ForbiddenPath",
                    "error_message": "Requested path is outside the app directory.",
                },
            )
            return

        if request_path in ("", "/"):
            candidate_path = app_dir / "index.html"

        if not candidate_path.exists() or candidate_path.is_dir():
            candidate_path = app_dir / "index.html"

        if not candidate_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "index.html not found")
            return

        content_type, _ = mimetypes.guess_type(str(candidate_path))
        if content_type is None:
            content_type = "application/octet-stream"

        body = candidate_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.end_headers()
        self.wfile.write(body)


def build_dashboard_server(
    *,
    host: str,
    port: int,
    trade_date: str,
    ops_dir: str | None,
    daily_report_input: str | None,
    rehearsal_input: str | None,
    app_dir: str | Path,
    daily_check_input: str | None = None,
    db_path: str | None = None,
) -> DashboardThreadingHTTPServer:
    resolved_app_dir = dashboard_snapshot_script._resolve_path(str(app_dir))
    if not resolved_app_dir.exists():
        raise FileNotFoundError(f"App directory does not exist: {resolved_app_dir}")
    if not resolved_app_dir.is_dir():
        raise NotADirectoryError(f"App directory is not a directory: {resolved_app_dir}")

    config = DashboardServerConfig(
        host=host,
        port=port,
        trade_date=_validate_trade_date(trade_date),
        ops_dir=_optional_text(ops_dir),
        daily_report_input=_optional_text(daily_report_input),
        daily_check_input=_optional_text(daily_check_input),
        rehearsal_input=_optional_text(rehearsal_input),
        app_dir=resolved_app_dir.resolve(),
        db_path=_optional_text(db_path),
    )
    server = DashboardThreadingHTTPServer(
        (host, port),
        DashboardAppRequestHandler,
        config=config,
    )
    if config.db_path is not None:
        _ensure_db_ready(server)
    return server


def main() -> int:
    args = _parse_args()

    try:
        server = build_dashboard_server(
            host=args.host,
            port=args.port,
            trade_date=args.trade_date,
            ops_dir=args.ops_dir,
            daily_report_input=args.daily_report_input,
            daily_check_input=args.daily_check_input,
            rehearsal_input=args.rehearsal_input,
            app_dir=args.app_dir,
            db_path=args.db_path,
        )
    except Exception as exc:
        _fail("dashboard_server", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Dashboard Server")
    _ok("host", args.host)
    _ok("port", str(args.port))
    _ok("trade_date", args.trade_date)
    _ok("app_dir", str(server.config.app_dir))
    if args.ops_dir:
        _ok("ops_dir", args.ops_dir)
    _warn("open", f"http://{args.host}:{args.port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _warn("shutdown", "Interrupted by user.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
