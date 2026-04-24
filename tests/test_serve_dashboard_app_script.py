"""Tests for serve_dashboard_app.py."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import scripts.serve_dashboard_app as target


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_build_dashboard_server_serves_index_file(
    test_db_path,
):
    app_dir = test_db_path.with_name(f"{test_db_path.stem}_dashboard_server_app")
    _write_text(app_dir / "index.html", "<!doctype html><title>dashboard</title>")

    server = target.build_dashboard_server(
        host="127.0.0.1",
        port=0,
        trade_date="2026-04-20",
        ops_dir=None,
        daily_report_input=None,
        rehearsal_input=None,
        app_dir=app_dir,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
            body = response.read().decode("utf-8")
            assert response.status == 200
            assert "<title>dashboard</title>" in body
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_dashboard_snapshot_api_returns_payload_and_validates_trade_date(
    test_db_path,
):
    app_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_api_app"
    )
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_api_ops"
    )
    _write_text(app_dir / "index.html", "<!doctype html><title>dashboard</title>")
    _write_json(
        ops_dir / "daily_ops_report.json",
        {
            "trade_date": "2026-04-20",
            "artifact_count": 1,
            "report_outcome": "READY",
            "health_outcome": "READY",
            "highest_severity": "NONE",
            "attention_flags": [],
            "action_items": [],
            "alert": {
                "critical_count": 0,
                "warning_count": 0,
            },
            "artifacts": {
                "trading_session_preview": {
                    "exists": True,
                    "status_level": "READY",
                    "highest_severity": "NONE",
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                    "preopen_readiness_outcome": "READY",
                    "preopen_readiness_reason": None,
                    "polling_started": True,
                    "polling_exit_code": 0,
                    "polling_stop_reason": "MAX_CYCLES_REACHED",
                    "timing2_setup_required": False,
                    "timing2_setup_ready": None,
                    "timing2_setup_signal_count": None,
                    "attention_flags": [],
                }
            },
        },
    )

    server = target.build_dashboard_server(
        host="127.0.0.1",
        port=0,
        trade_date="2026-04-20",
        ops_dir=str(ops_dir),
        daily_report_input=None,
        rehearsal_input=None,
        app_dir=app_dir,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/dashboard-snapshot",
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["trade_date"] == "2026-04-20"
            assert payload["overview"]["daily_report_available"] is True
            assert payload["scan"]["live_preview"]["session_outcome"] == "COMPLETED"

        try:
            urlopen(
                f"http://127.0.0.1:{server.server_port}/api/dashboard-snapshot?trade_date=bad-date",
                timeout=5,
            )
            raise AssertionError("Expected HTTPError for invalid trade_date")
        except HTTPError as exc:
            assert exc.code == 400
            error_payload = json.loads(exc.read().decode("utf-8"))
            assert error_payload["error_type"] == "ValueError"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
