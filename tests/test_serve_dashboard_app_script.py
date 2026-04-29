"""Tests for serve_dashboard_app.py."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import scripts.serve_dashboard_app as target
from storage.db import get_connection
from storage.repositories import TradingControlRepository


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


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


def test_dashboard_snapshot_api_uses_daily_ops_check_override(
    test_db_path,
):
    app_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_daily_check_override_app"
    )
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_daily_check_override_ops"
    )
    override_path = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_daily_check_override.json"
    )
    _write_text(app_dir / "index.html", "<!doctype html><title>dashboard</title>")
    _write_json(
        override_path,
        {
            "trade_date": "2026-04-20",
            "overall_outcome": "NOTIFICATION_REQUIRED",
            "overall_reason": "Override summary should be used.",
            "should_notify": True,
            "operator_summary": {
                "headline": "Override operator summary",
                "detail": "Affected symbols: 005930 | Review executions first.",
                "health_outcome": "WARNING",
                "dispatch_outcome": "DISPATCHED",
                "primary_attention_flag": "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
                "primary_action_code": "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK",
                "startup_open_entry_lot_position_mismatch": True,
                "affected_symbols": "005930",
            },
        },
    )

    server = target.build_dashboard_server(
        host="127.0.0.1",
        port=0,
        trade_date="2026-04-20",
        ops_dir=str(ops_dir),
        daily_report_input=None,
        daily_check_input=str(override_path),
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
            assert payload["sources"]["daily_report_available"] is False
            assert payload["sources"]["daily_ops_check_available"] is True
            assert payload["sources"]["daily_ops_check_path"] == str(override_path)
            assert payload["operator_summary"]["source"] == "daily_ops_check"
            assert payload["operator_summary"]["headline"] == "Override operator summary"
            assert payload["operator_summary"]["affected_symbols"] == "005930"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_kill_switch_api_persists_db_row_and_ops_artifact(test_db_path):
    app_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_kill_switch_app"
    )
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_kill_switch_ops"
    )
    _write_text(app_dir / "index.html", "<!doctype html><title>dashboard</title>")

    server = target.build_dashboard_server(
        host="127.0.0.1",
        port=0,
        trade_date="2026-04-20",
        ops_dir=str(ops_dir),
        daily_report_input=None,
        rehearsal_input=None,
        app_dir=app_dir,
        db_path=str(test_db_path),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        payload = _post_json(
            f"http://127.0.0.1:{server.server_port}/api/kill-switch",
            {
                "enabled": True,
                "note": "manual emergency stop",
                "trade_date": "2026-04-20",
            },
        )

        assert payload["ok"] is True
        assert payload["action"] == "ENABLE"
        assert payload["controls"]["kill_switch_enabled"] is True
        assert payload["controls"]["kill_switch_status_level"] == "CRITICAL"

        artifact_path = ops_dir / "kill_switch.enable.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["enabled"] is True
        assert artifact["note"] == "manual emergency stop"

        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/dashboard-snapshot",
            timeout=5,
        ) as response:
            snapshot = json.loads(response.read().decode("utf-8"))
        assert snapshot["controls"]["kill_switch_enabled"] is True
        assert snapshot["controls"]["kill_switch_status_level"] == "CRITICAL"

        conn = get_connection(test_db_path)
        try:
            row = TradingControlRepository(conn).get_kill_switch()
        finally:
            conn.close()
        assert row is not None
        assert row.is_enabled is True
        assert row.note == "manual emergency stop"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_kill_switch_api_requires_note_when_disabling(test_db_path):
    app_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_kill_switch_validation_app"
    )
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_kill_switch_validation_ops"
    )
    _write_text(app_dir / "index.html", "<!doctype html><title>dashboard</title>")

    server = target.build_dashboard_server(
        host="127.0.0.1",
        port=0,
        trade_date="2026-04-20",
        ops_dir=str(ops_dir),
        daily_report_input=None,
        rehearsal_input=None,
        app_dir=app_dir,
        db_path=str(test_db_path),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        try:
            _post_json(
                f"http://127.0.0.1:{server.server_port}/api/kill-switch",
                {
                    "enabled": False,
                    "note": "",
                    "trade_date": "2026-04-20",
                },
            )
            raise AssertionError("Expected HTTPError for missing disable note")
        except HTTPError as exc:
            assert exc.code == 400
            error_payload = json.loads(exc.read().decode("utf-8"))
            assert error_payload["error_type"] == "ValueError"
            assert "note is required" in error_payload["error_message"]

        assert not (ops_dir / "kill_switch.disable.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_buy_strategy_api_persists_selection_artifact_and_snapshot(test_db_path):
    app_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_buy_strategy_app"
    )
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_buy_strategy_ops"
    )
    _write_text(app_dir / "index.html", "<!doctype html><title>dashboard</title>")

    server = target.build_dashboard_server(
        host="127.0.0.1",
        port=0,
        trade_date="2026-04-20",
        ops_dir=str(ops_dir),
        daily_report_input=None,
        rehearsal_input=None,
        app_dir=app_dir,
        db_path=str(test_db_path),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        payload = _post_json(
            f"http://127.0.0.1:{server.server_port}/api/buy-strategy",
            {
                "buy_strategy": "timing2",
                "note": "focus on revised timing2",
                "trade_date": "2026-04-20",
            },
        )

        assert payload["ok"] is True
        assert payload["strategy"]["buy_strategy"] == "timing2"
        assert payload["strategy"]["run_timing1"] is False
        assert payload["strategy"]["run_timing2"] is True
        assert payload["strategy"]["applies_to_next_run"] is True

        artifact_path = ops_dir / "buy_strategy.selection.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["buy_strategy"] == "timing2"
        assert artifact["note"] == "focus on revised timing2"

        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/dashboard-snapshot",
            timeout=5,
        ) as response:
            snapshot = json.loads(response.read().decode("utf-8"))
        assert snapshot["strategy"]["selection_available"] is True
        assert snapshot["strategy"]["buy_strategy"] == "timing2"
        assert snapshot["strategy"]["run_timing1"] is False
        assert snapshot["strategy"]["run_timing2"] is True
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_buy_strategy_api_rejects_invalid_strategy(test_db_path):
    app_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_buy_strategy_invalid_app"
    )
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_server_buy_strategy_invalid_ops"
    )
    _write_text(app_dir / "index.html", "<!doctype html><title>dashboard</title>")

    server = target.build_dashboard_server(
        host="127.0.0.1",
        port=0,
        trade_date="2026-04-20",
        ops_dir=str(ops_dir),
        daily_report_input=None,
        rehearsal_input=None,
        app_dir=app_dir,
        db_path=str(test_db_path),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        try:
            _post_json(
                f"http://127.0.0.1:{server.server_port}/api/buy-strategy",
                {
                    "buy_strategy": "timing3",
                    "trade_date": "2026-04-20",
                },
            )
            raise AssertionError("Expected HTTPError for invalid buy_strategy")
        except HTTPError as exc:
            assert exc.code == 400
            error_payload = json.loads(exc.read().decode("utf-8"))
            assert error_payload["error_type"] == "ValueError"
            assert "buy_strategy must be one of" in error_payload["error_message"]

        assert not (ops_dir / "buy_strategy.selection.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
