"""
Run one operator-safe preview check in one command.

Flow:
1. Run run_trading_session.py in preview mode.
2. Run run_daily_ops_check.py against the same ops directory.
3. Build one dashboard snapshot from the resulting artifacts.

Safety:
- preview only; execute mode is not exposed here
- reuses existing scripts instead of duplicating trading logic
- produces one combined JSON summary for operator review
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging

KST = pytz.timezone("Asia/Seoul")
RUN_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


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
        description="Run one operator-safe preview check for trading session, daily ops, and dashboard snapshot."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--master-input",
        default=None,
        help="Optional JSON or CSV market master input path.",
    )
    parser.add_argument(
        "--master-format",
        default="auto",
        choices=("auto", "json", "csv"),
        help="Market master input format. Default: auto",
    )
    parser.add_argument(
        "--use-db-master",
        action="store_true",
        help="Use the current market master snapshot already stored in SQLite.",
    )
    parser.add_argument(
        "--require-same-day-master",
        action="store_true",
        help="Block the run if market master refreshed date does not match trade_date.",
    )
    parser.add_argument(
        "--allow-validation-failures",
        action="store_true",
        help="Continue even if market master validation emits warnings.",
    )
    parser.add_argument(
        "--allow-unresolved-orders",
        action="store_true",
        help="Allow preopen checks to continue even if unresolved orders exist.",
    )
    parser.add_argument(
        "--allow-empty-save",
        action="store_true",
        help="Allow saving an empty universe snapshot when accepted_count is 0.",
    )
    parser.add_argument(
        "--per-order-budget",
        type=int,
        required=True,
        help="Max KRW budget per buy order for preview.",
    )
    parser.add_argument(
        "--max-holdings",
        type=int,
        required=True,
        help="Max concurrent holdings/unresolved-buy symbols for preview.",
    )
    parser.add_argument(
        "--max-daily-order-count",
        type=int,
        default=None,
        help="Optional max total order count for the trade date.",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=int,
        default=None,
        help="Optional max realized daily loss in KRW.",
    )
    parser.add_argument(
        "--buy-strategy",
        choices=("timing1", "timing2", "both"),
        default=None,
        help="Optional explicit buy strategy selection.",
    )
    parser.add_argument(
        "--scan-timing1",
        action="store_true",
        help="Run timing1 intraday trigger scan during preview polling.",
    )
    parser.add_argument(
        "--scan-timing2",
        action="store_true",
        help="Run timing2 intraday trigger scan during preview polling.",
    )
    parser.add_argument(
        "--preopen-scan-timing2-setup",
        action="store_true",
        help="Run timing2 daily setup scan during preopen.",
    )
    parser.add_argument(
        "--preopen-write-timing2-signals",
        action="store_true",
        help="Persist timing2 setup signals during preopen.",
    )
    parser.add_argument(
        "--preopen-timing2-daily-count",
        type=int,
        default=90,
        help="Daily candle count for timing2 setup scan. Default: 90",
    )
    parser.add_argument(
        "--preopen-timing2-new-high-lookback-days",
        type=int,
        default=60,
        help="Timing2 setup new-high lookback window. Default: 60",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=1,
        help="Preview polling interval seconds. Default: 1",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=1,
        help="Preview polling cycle count. Default: 1",
    )
    parser.add_argument(
        "--polling-lock-name",
        default=None,
        help="Optional polling runtime lock name override.",
    )
    parser.add_argument(
        "--ops-dir",
        default=None,
        help="Optional ops directory override. Default: data/ops/<trade-date>_preview_<strategy>/<run-label>",
    )
    parser.add_argument(
        "--run-label",
        default=None,
        help="Optional preview run label for the default ops directory. Default: KST timestamp",
    )
    parser.add_argument(
        "--notify-min-level",
        choices=("READY", "WARNING", "CRITICAL", "NO_ARTIFACTS"),
        default="WARNING",
        help="Minimum health_outcome level that should trigger notification. Default: WARNING",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional combined summary JSON output path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _resolve_preview_slug(args: argparse.Namespace) -> str:
    explicit_strategy = _optional_text(getattr(args, "buy_strategy", None))
    if explicit_strategy in {"timing1", "timing2", "both"}:
        return explicit_strategy

    scan_timing1 = bool(getattr(args, "scan_timing1", False))
    scan_timing2 = bool(getattr(args, "scan_timing2", False))
    timing2_setup = bool(getattr(args, "preopen_scan_timing2_setup", False)) or bool(
        getattr(args, "preopen_write_timing2_signals", False)
    )

    if scan_timing1 and scan_timing2:
        return "both"
    if scan_timing2 or timing2_setup:
        return "timing2"
    if scan_timing1:
        return "timing1"
    return "generic"


def _resolve_run_label(
    args: argparse.Namespace,
    *,
    current_time: datetime | None = None,
) -> str:
    explicit_run_label = _optional_text(getattr(args, "run_label", None))
    if explicit_run_label is not None:
        if RUN_LABEL_PATTERN.fullmatch(explicit_run_label) is None:
            raise ValueError(
                "--run-label may contain only letters, numbers, dots, underscores, and hyphens."
            )
        return explicit_run_label

    explicit_ops_dir = _optional_text(getattr(args, "ops_dir", None))
    if explicit_ops_dir is not None:
        return _resolve_path(explicit_ops_dir).name

    resolved_time = current_time or datetime.now(KST)
    return resolved_time.strftime("%Y%m%d_%H%M%S_%f")


def _resolve_ops_dir(args: argparse.Namespace, *, run_label: str) -> Path:
    if args.ops_dir:
        return _resolve_path(args.ops_dir)
    preview_slug = _resolve_preview_slug(args)
    return (
        PROJECT_ROOT
        / "data"
        / "ops"
        / f"{args.trade_date}_preview_{preview_slug}"
        / run_label
    ).resolve()


def _resolve_polling_lock_name(args: argparse.Namespace) -> str:
    explicit_lock_name = _optional_text(getattr(args, "polling_lock_name", None))
    if explicit_lock_name is not None:
        return explicit_lock_name

    preview_slug = _resolve_preview_slug(args)
    return f"intraday_trading_polling:{args.trade_date}:preview-{preview_slug}"


def _resolve_effective_timing2_preopen_options(
    args: argparse.Namespace,
) -> tuple[bool, bool]:
    scan_timing2_setup = bool(getattr(args, "preopen_scan_timing2_setup", False))
    write_timing2_signals = bool(
        getattr(args, "preopen_write_timing2_signals", False)
    )
    buy_strategy = _optional_text(getattr(args, "buy_strategy", None))

    if write_timing2_signals and not scan_timing2_setup:
        scan_timing2_setup = True

    if buy_strategy in {"timing2", "both"} and not scan_timing2_setup:
        scan_timing2_setup = True
        write_timing2_signals = True

    return scan_timing2_setup, write_timing2_signals


def _resolve_output_path(args: argparse.Namespace, ops_dir: Path) -> Path:
    if args.output:
        return _resolve_path(args.output)
    return ops_dir / "operational_preview_check.json"


def _check_master_source_args(args: argparse.Namespace) -> None:
    has_master_input = bool(args.master_input)
    if args.use_db_master == has_master_input:
        raise ValueError(
            "Exactly one of --master-input or --use-db-master must be provided."
        )


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _run_child(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    return int(completed.returncode)


def _step_payload(
    *,
    name: str,
    exit_code: int | None,
    outcome: str,
    reason: str | None,
    output_path: Path,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "name": name,
        "exit_code": exit_code,
        "outcome": outcome,
        "reason": reason,
        "output_path": str(output_path),
        "result": result,
    }


def _append_option(command: list[str], flag: str, value: Any | None) -> None:
    if value is None:
        return
    command.extend([flag, str(value)])


def _build_trading_session_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
    polling_lock_name: str,
    effective_scan_timing2_setup: bool,
    effective_write_timing2_signals: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_trading_session.py"),
        "--trade-date",
        args.trade_date,
        "--per-order-budget",
        str(args.per_order_budget),
        "--max-holdings",
        str(args.max_holdings),
        "--interval-seconds",
        str(args.interval_seconds),
        "--max-cycles",
        str(args.max_cycles),
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]
    if args.use_db_master:
        command.append("--use-db-master")
    else:
        command.extend(["--master-input", str(_resolve_path(args.master_input))])
        command.extend(["--master-format", args.master_format])
    if args.require_same_day_master:
        command.append("--require-same-day-master")
    if args.allow_validation_failures:
        command.append("--allow-validation-failures")
    if args.allow_unresolved_orders:
        command.append("--allow-unresolved-orders")
    if args.allow_empty_save:
        command.append("--allow-empty-save")
    _append_option(command, "--max-daily-order-count", args.max_daily_order_count)
    _append_option(command, "--max-daily-loss", args.max_daily_loss)
    if args.buy_strategy:
        command.extend(["--buy-strategy", args.buy_strategy])
    if args.scan_timing1:
        command.append("--scan-timing1")
    if args.scan_timing2:
        command.append("--scan-timing2")
    if effective_scan_timing2_setup:
        command.append("--preopen-scan-timing2-setup")
    if effective_write_timing2_signals:
        command.append("--preopen-write-timing2-signals")
    command.extend(
        ["--preopen-timing2-daily-count", str(args.preopen_timing2_daily_count)]
    )
    command.extend(
        [
            "--preopen-timing2-new-high-lookback-days",
            str(args.preopen_timing2_new_high_lookback_days),
        ]
    )
    command.extend(["--polling-lock-name", polling_lock_name])
    return command


def _build_daily_ops_check_command(
    *,
    args: argparse.Namespace,
    ops_dir: Path,
    output_path: Path,
) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_daily_ops_check.py"),
        "--trade-date",
        args.trade_date,
        "--ops-dir",
        str(ops_dir),
        "--notify-min-level",
        args.notify_min_level,
        "--output",
        str(output_path),
    ]


def _build_dashboard_snapshot_command(
    *,
    args: argparse.Namespace,
    ops_dir: Path,
    output_path: Path,
) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_dashboard_snapshot.py"),
        "--trade-date",
        args.trade_date,
        "--ops-dir",
        str(ops_dir),
        "--output",
        str(output_path),
    ]


def _resolve_trading_session_step(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "run_trading_session.py did not write a JSON payload."
    session_outcome = _optional_text(payload.get("session_outcome")) or "UNKNOWN"
    session_reason = _optional_text(payload.get("session_reason"))
    if session_outcome == "COMPLETED" and exit_code == 0:
        return "COMPLETED", None
    if session_outcome.endswith("_BLOCKED") or session_outcome == "POLLING_LOCK_BUSY":
        return "BLOCKED", session_reason or session_outcome
    return "FAILED", session_reason or f"Unexpected session_outcome={session_outcome}"


def _resolve_daily_ops_step(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "run_daily_ops_check.py did not write a JSON payload."
    overall_outcome = _optional_text(payload.get("overall_outcome")) or "UNKNOWN"
    overall_reason = _optional_text(payload.get("overall_reason"))
    if exit_code == 0 and overall_outcome == "READY":
        return "COMPLETED", None
    if overall_outcome == "NOTIFICATION_REQUIRED":
        return "ATTENTION", overall_reason or overall_outcome
    return "FAILED", overall_reason or f"Unexpected daily ops outcome={overall_outcome}"


def _resolve_dashboard_step(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "build_dashboard_snapshot.py did not write a JSON payload."
    status_level = _optional_text(payload.get("operator_summary", {}).get("status_level"))
    if exit_code == 0 and status_level in {"READY", "WARNING", "CRITICAL", "MISSING"}:
        return "COMPLETED", None
    return "FAILED", f"Unexpected dashboard status_level={status_level or 'UNKNOWN'}"


def main() -> int:
    args = _parse_args()

    try:
        _check_master_source_args(args)
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("preview_check", f"{type(exc).__name__}: {exc}")
        return 5

    started_at = datetime.now(KST)
    db_path = args.db_path or settings.db_path
    try:
        run_label = _resolve_run_label(args, current_time=started_at)
        ops_dir = _resolve_ops_dir(args, run_label=run_label)
    except Exception as exc:
        _fail("preview_check", f"{type(exc).__name__}: {exc}")
        return 5
    output_path = _resolve_output_path(args, ops_dir)
    polling_lock_name = _resolve_polling_lock_name(args)
    (
        effective_scan_timing2_setup,
        effective_write_timing2_signals,
    ) = _resolve_effective_timing2_preopen_options(args)
    trading_output_path = ops_dir / "run_trading_session.preview.json"
    daily_ops_output_path = ops_dir / "daily_ops_check.preview.json"
    dashboard_output_path = ops_dir / "dashboard_snapshot.preview.json"

    steps: list[dict[str, Any]] = []

    _section("Run Operational Preview Check")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("run_label", run_label)
    _ok("ops_dir", str(ops_dir))
    _ok("db_path", str(db_path))
    _ok("polling_lock_name", polling_lock_name)
    _ok("preopen_scan_timing2_setup", str(effective_scan_timing2_setup))
    _ok("preopen_write_timing2_signals", str(effective_write_timing2_signals))
    _ok("preview_only", "True")

    trading_command = _build_trading_session_command(
        args=args,
        db_path=db_path,
        output_path=trading_output_path,
        polling_lock_name=polling_lock_name,
        effective_scan_timing2_setup=effective_scan_timing2_setup,
        effective_write_timing2_signals=effective_write_timing2_signals,
    )
    trading_exit_code = _run_child(trading_command)
    trading_payload = _load_json(trading_output_path)
    trading_status, trading_reason = _resolve_trading_session_step(
        exit_code=trading_exit_code,
        payload=trading_payload,
    )
    steps.append(
        _step_payload(
            name="Trading Session Preview",
            exit_code=trading_exit_code,
            outcome=trading_status,
            reason=trading_reason,
            output_path=trading_output_path,
            result=trading_payload,
        )
    )

    daily_ops_command = _build_daily_ops_check_command(
        args=args,
        ops_dir=ops_dir,
        output_path=daily_ops_output_path,
    )
    daily_ops_exit_code = _run_child(daily_ops_command)
    daily_ops_payload = _load_json(daily_ops_output_path)
    daily_ops_status, daily_ops_reason = _resolve_daily_ops_step(
        exit_code=daily_ops_exit_code,
        payload=daily_ops_payload,
    )
    steps.append(
        _step_payload(
            name="Daily Ops Check",
            exit_code=daily_ops_exit_code,
            outcome=daily_ops_status,
            reason=daily_ops_reason,
            output_path=daily_ops_output_path,
            result=daily_ops_payload,
        )
    )

    dashboard_command = _build_dashboard_snapshot_command(
        args=args,
        ops_dir=ops_dir,
        output_path=dashboard_output_path,
    )
    dashboard_exit_code = _run_child(dashboard_command)
    dashboard_payload = _load_json(dashboard_output_path)
    dashboard_status, dashboard_reason = _resolve_dashboard_step(
        exit_code=dashboard_exit_code,
        payload=dashboard_payload,
    )
    steps.append(
        _step_payload(
            name="Dashboard Snapshot",
            exit_code=dashboard_exit_code,
            outcome=dashboard_status,
            reason=dashboard_reason,
            output_path=dashboard_output_path,
            result=dashboard_payload,
        )
    )

    overall_outcome = "COMPLETED"
    overall_reason: str | None = None
    if trading_status == "FAILED":
        overall_outcome = "TRADING_SESSION_FAILED"
        overall_reason = trading_reason
    elif trading_status == "BLOCKED":
        overall_outcome = "TRADING_SESSION_BLOCKED"
        overall_reason = trading_reason
    elif daily_ops_status == "FAILED":
        overall_outcome = "DAILY_OPS_FAILED"
        overall_reason = daily_ops_reason
    elif daily_ops_status == "ATTENTION":
        overall_outcome = "DAILY_OPS_ATTENTION"
        overall_reason = daily_ops_reason
    elif dashboard_status == "FAILED":
        overall_outcome = "DASHBOARD_FAILED"
        overall_reason = dashboard_reason

    finished_at = datetime.now(KST)
    payload = {
        "trade_date": args.trade_date,
        "run_label": run_label,
        "mode": settings.mode,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "ops_dir": str(ops_dir),
        "db_path": str(db_path),
        "polling_lock_name": polling_lock_name,
        "preopen_scan_timing2_setup": effective_scan_timing2_setup,
        "preopen_write_timing2_signals": effective_write_timing2_signals,
        "overall_outcome": overall_outcome,
        "overall_reason": overall_reason,
        "steps": steps,
        "artifacts": {
            "trading_session_output": str(trading_output_path),
            "daily_ops_output": str(daily_ops_output_path),
            "dashboard_output": str(dashboard_output_path),
        },
    }
    _save_json(output_path, payload)

    _section("Operational Preview Result")
    _ok("overall_outcome", overall_outcome)
    if overall_reason:
        _warn("overall_reason", overall_reason)
    _ok("json_saved", str(output_path))

    if overall_outcome == "COMPLETED":
        return 0
    if overall_outcome.endswith("_BLOCKED"):
        return 4
    if overall_outcome.endswith("_ATTENTION"):
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
