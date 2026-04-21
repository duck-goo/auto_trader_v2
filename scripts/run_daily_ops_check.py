"""
Run daily ops report generation plus notification preparation in one command.

Flow:
1. Run show_daily_ops_report.py.
2. Run prepare_daily_ops_notification.py from the generated report.
3. Optionally run send_daily_ops_notification.py.

Safety:
- read-only for source ops artifacts
- creates only derived report/alert/notification/dispatch files
- dispatch is opt-in and disabled by default
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

KST = pytz.timezone("Asia/Seoul")


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
        description="Run daily ops report generation plus notification preparation."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--ops-dir",
        default=None,
        help="Optional ops directory override. Default: data/ops/<trade-date>",
    )
    parser.add_argument(
        "--strict-report",
        action="store_true",
        help="Pass --strict to show_daily_ops_report.py.",
    )
    parser.add_argument(
        "--notify-min-level",
        choices=("READY", "WARNING", "CRITICAL", "NO_ARTIFACTS"),
        default="WARNING",
        help="Minimum health_outcome level that should trigger notification.",
    )
    parser.add_argument(
        "--dispatch",
        action="store_true",
        help="Also run send_daily_ops_notification.py after notification preparation.",
    )
    parser.add_argument(
        "--force-dispatch",
        action="store_true",
        help="Pass --force to send_daily_ops_notification.py.",
    )
    parser.add_argument(
        "--report-output",
        default=None,
        help="Optional daily ops report JSON output path.",
    )
    parser.add_argument(
        "--alert-output",
        default=None,
        help="Optional daily ops alert text output path.",
    )
    parser.add_argument(
        "--notification-output",
        default=None,
        help="Optional notification JSON output path.",
    )
    parser.add_argument(
        "--notification-text-output",
        default=None,
        help="Optional notification text output path.",
    )
    parser.add_argument(
        "--dispatch-record-output",
        default=None,
        help="Optional dispatch record JSON output path.",
    )
    parser.add_argument(
        "--dispatch-text-output",
        default=None,
        help="Optional dispatched text output path.",
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


def _resolve_ops_dir(args: argparse.Namespace) -> Path:
    if args.ops_dir:
        return _resolve_path(args.ops_dir)
    return (PROJECT_ROOT / "data" / "ops" / args.trade_date).resolve()


def _resolve_output_paths(
    *,
    args: argparse.Namespace,
    ops_dir: Path,
) -> dict[str, Path]:
    return {
        "report_output": (
            _resolve_path(args.report_output)
            if args.report_output
            else ops_dir / "daily_ops_report.json"
        ),
        "alert_output": (
            _resolve_path(args.alert_output)
            if args.alert_output
            else ops_dir / "daily_ops_alert.txt"
        ),
        "notification_output": (
            _resolve_path(args.notification_output)
            if args.notification_output
            else ops_dir / "daily_ops_notification.json"
        ),
        "notification_text_output": (
            _resolve_path(args.notification_text_output)
            if args.notification_text_output
            else ops_dir / "daily_ops_notification.txt"
        ),
        "dispatch_record_output": (
            _resolve_path(args.dispatch_record_output)
            if args.dispatch_record_output
            else ops_dir / "daily_ops_dispatch.json"
        ),
        "dispatch_text_output": (
            _resolve_path(args.dispatch_text_output)
            if args.dispatch_text_output
            else ops_dir / "daily_ops_dispatched.txt"
        ),
        "summary_output": (
            _resolve_path(args.output)
            if args.output
            else ops_dir / "daily_ops_check.json"
        ),
    }


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


def _build_report_command(
    *,
    args: argparse.Namespace,
    ops_dir: Path,
    report_output: Path,
    alert_output: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "show_daily_ops_report.py"),
        "--trade-date",
        args.trade_date,
        "--ops-dir",
        str(ops_dir),
        "--output",
        str(report_output),
        "--alert-output",
        str(alert_output),
    ]
    if args.strict_report:
        command.append("--strict")
    return command


def _build_notification_command(
    *,
    args: argparse.Namespace,
    report_output: Path,
    notification_output: Path,
    notification_text_output: Path,
) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "prepare_daily_ops_notification.py"),
        "--input",
        str(report_output),
        "--min-level",
        args.notify_min_level,
        "--output",
        str(notification_output),
        "--text-output",
        str(notification_text_output),
    ]


def _build_dispatch_command(
    *,
    args: argparse.Namespace,
    notification_output: Path,
    dispatch_record_output: Path,
    dispatch_text_output: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "send_daily_ops_notification.py"),
        "--input",
        str(notification_output),
        "--record-output",
        str(dispatch_record_output),
        "--dispatch-text-output",
        str(dispatch_text_output),
    ]
    if args.force_dispatch:
        command.append("--force")
    return command


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


def _report_step_outcome(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "show_daily_ops_report.py did not write a JSON payload."
    health_outcome = _optional_text(payload.get("health_outcome")) or "UNKNOWN"
    report_outcome = _optional_text(payload.get("report_outcome")) or "UNKNOWN"
    if exit_code in (0, 4, 5):
        return "COMPLETED", (
            f"exit_code={exit_code}, report_outcome={report_outcome}, health_outcome={health_outcome}"
        )
    return "FAILED", (
        f"Unexpected report result: exit_code={exit_code}, "
        f"report_outcome={report_outcome}, health_outcome={health_outcome}"
    )


def _notification_step_outcome(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "prepare_daily_ops_notification.py did not write a JSON payload."
    should_notify = payload.get("should_notify")
    reason = _optional_text(payload.get("notification_reason"))
    if exit_code in (0, 4):
        return "COMPLETED", reason or f"should_notify={should_notify}"
    return "FAILED", reason or f"Unexpected notification result: exit_code={exit_code}"


def _dispatch_step_outcome(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "send_daily_ops_notification.py did not write a JSON payload."
    outcome = _optional_text(payload.get("outcome")) or "UNKNOWN"
    reason = _optional_text(payload.get("reason"))
    if exit_code in (0, 4):
        return "COMPLETED", reason or f"outcome={outcome}"
    return "FAILED", reason or f"Unexpected dispatch result: exit_code={exit_code}, outcome={outcome}"


def main() -> int:
    args = _parse_args()

    try:
        ops_dir = _resolve_ops_dir(args)
        ops_dir.mkdir(parents=True, exist_ok=True)
        output_paths = _resolve_output_paths(args=args, ops_dir=ops_dir)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    started_at = datetime.now(KST)
    steps: list[dict[str, Any]] = []

    _section("Run Daily Ops Check")
    _ok("trade_date", args.trade_date)
    _ok("ops_dir", str(ops_dir))
    _ok("strict_report", str(args.strict_report))
    _ok("notify_min_level", args.notify_min_level)
    _ok("dispatch", str(args.dispatch))
    _ok("force_dispatch", str(args.force_dispatch))

    report_command = _build_report_command(
        args=args,
        ops_dir=ops_dir,
        report_output=output_paths["report_output"],
        alert_output=output_paths["alert_output"],
    )
    report_exit_code = _run_child(report_command)
    report_payload = _load_json(output_paths["report_output"])
    report_step_status, report_reason = _report_step_outcome(
        exit_code=report_exit_code,
        payload=report_payload,
    )
    steps.append(
        _step_payload(
            name="Build Daily Ops Report",
            exit_code=report_exit_code,
            outcome=report_step_status,
            reason=report_reason,
            output_path=output_paths["report_output"],
            result=report_payload,
        )
    )

    if report_step_status != "COMPLETED":
        overall_outcome = "REPORT_FAILED"
        overall_reason = report_reason
    else:
        notification_command = _build_notification_command(
            args=args,
            report_output=output_paths["report_output"],
            notification_output=output_paths["notification_output"],
            notification_text_output=output_paths["notification_text_output"],
        )
        notification_exit_code = _run_child(notification_command)
        notification_payload = _load_json(output_paths["notification_output"])
        notification_step_status, notification_reason = _notification_step_outcome(
            exit_code=notification_exit_code,
            payload=notification_payload,
        )
        steps.append(
            _step_payload(
                name="Prepare Daily Ops Notification",
                exit_code=notification_exit_code,
                outcome=notification_step_status,
                reason=notification_reason,
                output_path=output_paths["notification_output"],
                result=notification_payload,
            )
        )

        if notification_step_status != "COMPLETED":
            overall_outcome = "NOTIFICATION_FAILED"
            overall_reason = notification_reason
        elif notification_payload is not None and notification_payload.get("should_notify") is True:
            if not args.dispatch:
                overall_outcome = "NOTIFICATION_REQUIRED"
                overall_reason = _optional_text(notification_payload.get("notification_reason"))
            else:
                dispatch_command = _build_dispatch_command(
                    args=args,
                    notification_output=output_paths["notification_output"],
                    dispatch_record_output=output_paths["dispatch_record_output"],
                    dispatch_text_output=output_paths["dispatch_text_output"],
                )
                dispatch_exit_code = _run_child(dispatch_command)
                dispatch_payload = _load_json(output_paths["dispatch_record_output"])
                dispatch_step_status, dispatch_reason = _dispatch_step_outcome(
                    exit_code=dispatch_exit_code,
                    payload=dispatch_payload,
                )
                steps.append(
                    _step_payload(
                        name="Dispatch Daily Ops Notification",
                        exit_code=dispatch_exit_code,
                        outcome=dispatch_step_status,
                        reason=dispatch_reason,
                        output_path=output_paths["dispatch_record_output"],
                        result=dispatch_payload,
                    )
                )

                if dispatch_step_status != "COMPLETED":
                    overall_outcome = "DISPATCH_FAILED"
                    overall_reason = dispatch_reason
                else:
                    dispatch_outcome = (
                        None
                        if dispatch_payload is None
                        else _optional_text(dispatch_payload.get("outcome"))
                    )
                    dispatch_reason_text = (
                        None
                        if dispatch_payload is None
                        else _optional_text(dispatch_payload.get("reason"))
                    )
                    if dispatch_outcome in ("DISPATCHED", "FORCED_DISPATCHED"):
                        overall_outcome = "NOTIFICATION_DISPATCHED"
                        overall_reason = dispatch_reason_text
                    elif dispatch_outcome == "DUPLICATE_SKIPPED":
                        overall_outcome = "NOTIFICATION_DUPLICATE_SKIPPED"
                        overall_reason = dispatch_reason_text
                    elif dispatch_outcome == "NO_NOTIFICATION":
                        overall_outcome = "READY"
                        overall_reason = None
                    else:
                        overall_outcome = "DISPATCH_FAILED"
                        overall_reason = dispatch_reason or (
                            f"Unexpected dispatch outcome: {dispatch_outcome}"
                        )
        else:
            overall_outcome = "READY"
            overall_reason = None

    payload = {
        "trade_date": args.trade_date,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(KST).isoformat(),
        "ops_dir": str(ops_dir),
        "strict_report": args.strict_report,
        "notify_min_level": args.notify_min_level,
        "output_paths": {key: str(value) for key, value in output_paths.items()},
        "overall_outcome": overall_outcome,
        "overall_reason": overall_reason,
        "steps": steps,
    }
    _save_json(output_paths["summary_output"], payload)

    _section("Daily Ops Check Result")
    _ok("overall_outcome", overall_outcome)
    if overall_reason:
        _warn("overall_reason", overall_reason)
    _ok("json_saved", str(output_paths["summary_output"]))

    if overall_outcome in ("READY", "NOTIFICATION_DUPLICATE_SKIPPED"):
        return 0
    if overall_outcome in ("NOTIFICATION_REQUIRED", "NOTIFICATION_DISPATCHED"):
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
