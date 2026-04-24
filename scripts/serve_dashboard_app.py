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
import json
import mimetypes
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.build_dashboard_snapshot as dashboard_snapshot_script

KST = pytz.timezone("Asia/Seoul")
TRADE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
        "--rehearsal-input",
        default=None,
        help="Optional rehearsal_summary.json override.",
    )
    parser.add_argument(
        "--app-dir",
        default=str(PROJECT_ROOT / "ui" / "dashboard_app" / "dist"),
        help="Directory to serve static dashboard assets from.",
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


@dataclass(frozen=True)
class DashboardServerConfig:
    host: str
    port: int
    trade_date: str
    ops_dir: str | None
    daily_report_input: str | None
    rehearsal_input: str | None
    app_dir: Path


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
                rehearsal_input=self.server.config.rehearsal_input,
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

        self._send_json(HTTPStatus.OK, payload)

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
        rehearsal_input=_optional_text(rehearsal_input),
        app_dir=resolved_app_dir.resolve(),
    )
    return DashboardThreadingHTTPServer(
        (host, port),
        DashboardAppRequestHandler,
        config=config,
    )


def main() -> int:
    args = _parse_args()

    try:
        server = build_dashboard_server(
            host=args.host,
            port=args.port,
            trade_date=args.trade_date,
            ops_dir=args.ops_dir,
            daily_report_input=args.daily_report_input,
            rehearsal_input=args.rehearsal_input,
            app_dir=args.app_dir,
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
