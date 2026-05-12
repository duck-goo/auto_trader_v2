from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import scripts.run_order_maintenance as target


TRADE_DATE = "2026-05-11"


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeBroker:
    def __init__(self, settings) -> None:
        self.settings = settings

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummyService:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


class _SuccessfulRuntimeLockService:
    instances: list["_SuccessfulRuntimeLockService"] = []

    def __init__(self, conn, lock_repo) -> None:
        self.conn = conn
        self.lock_repo = lock_repo
        self.owner_id = "test-owner"
        self.acquired: list[tuple[str, int]] = []
        self.released: list[str] = []
        self.__class__.instances.append(self)

    def acquire(self, *, lock_name: str, lease_seconds: int) -> None:
        self.acquired.append((lock_name, lease_seconds))

    def release(self, *, lock_name: str) -> bool:
        self.released.append(lock_name)
        return True


class _BusyRuntimeLockService:
    instances: list["_BusyRuntimeLockService"] = []

    def __init__(self, conn, lock_repo) -> None:
        self.conn = conn
        self.lock_repo = lock_repo
        self.owner_id = "busy-owner"
        self.released: list[str] = []
        self.__class__.instances.append(self)

    def acquire(self, *, lock_name: str, lease_seconds: int) -> None:
        raise target.RuntimeLockBusyError(
            lock_name=lock_name,
            owner_id=self.owner_id,
            expires_at="2026-05-11T09:30:00+09:00",
        )

    def release(self, *, lock_name: str) -> bool:
        self.released.append(lock_name)
        return False


class _FakeMaintenanceService:
    result = None

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def run(
        self,
        *,
        trade_date: str,
        stale_cancel_settings,
        buy_signal_cleanup_settings,
        sell_signal_cleanup_settings,
        execute_changes: bool,
    ):
        assert trade_date == TRADE_DATE
        assert stale_cancel_settings.timeout_seconds == 120
        assert buy_signal_cleanup_settings is None
        assert sell_signal_cleanup_settings is None
        assert execute_changes is True
        return self.__class__.result


def _make_settings(test_db_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        mode="mock",
        db_path=str(test_db_path),
        db_busy_timeout_ms=5000,
    )


def _make_args(output_path: Path, *, execute: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=TRADE_DATE,
        timeout_seconds=120,
        buy_max_signal_age_seconds=None,
        sell_max_signal_age_seconds=None,
        signal_cleanup_limit=200,
        execute=execute,
        lock_name=None,
        lock_lease_seconds=180,
        limit=20,
        db_path=None,
        output=str(output_path),
    )


def _enum(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _make_maintenance_result() -> SimpleNamespace:
    sync_candidate = SimpleNamespace(
        client_order_id="sync-001",
        symbol="005930",
        status_before="PENDING",
        status_after="FILLED",
        kis_order_no="KIS-001",
        action=_enum("SYNC_ORDER"),
        outcome=_enum("SYNCED"),
        reason_code=None,
        reason_message=None,
        broker_status="FILLED",
        broker_filled_qty=1,
        acted=True,
    )
    recovery_candidate = SimpleNamespace(
        client_order_id="recover-001",
        symbol="000660",
        status_before="PARTIAL",
        status_after="FILLED",
        broker_status="FILLED",
        broker_filled_qty=2,
        local_execution_count=1,
        local_filled_qty=2,
        local_avg_fill_price=71000,
        action=_enum("FINALIZE_LOCAL_EXECUTION"),
        outcome=_enum("RECOVERED"),
        reason_code=None,
        reason_message=None,
        acted=True,
    )
    cancel_candidate = SimpleNamespace(
        client_order_id="cancel-001",
        symbol="035420",
        status="OPEN",
        requested_at="2026-05-11T09:00:00+09:00",
        age_seconds=900,
        outcome=_enum("CANCELLED"),
        reason_code="STALE_ORDER_TIMEOUT",
        reason_message="stale buy order",
        acted=True,
    )
    signal_candidate = SimpleNamespace(
        signal_id=10,
        symbol="051910",
        strategy_name="buy_timing1_intraday_trigger",
        scanned_at="2026-05-11T09:05:00+09:00",
        age_seconds=1200,
        outcome=_enum("PREVIEW_READY"),
        reason_code="STALE_SIGNAL_AGE_EXCEEDED",
        reason_message="signal is stale",
        acted=False,
    )
    return SimpleNamespace(
        trade_date=TRADE_DATE,
        execute_changes=True,
        manual_recovery_required_client_order_ids=["manual-001"],
        sync_result=SimpleNamespace(
            trade_date=TRADE_DATE,
            scanned_at="2026-05-11T09:10:00+09:00",
            execute_sync=True,
            unresolved_order_count=3,
            candidate_count=1,
            preview_ready_count=0,
            skipped_count=0,
            synced_count=1,
            execution_recovery_required_count=1,
            acted_count=1,
            candidates=[sync_candidate],
        ),
        execution_recovery_result=SimpleNamespace(
            trade_date=TRADE_DATE,
            scanned_at="2026-05-11T09:11:00+09:00",
            execute_recovery=True,
            candidate_count=1,
            preview_ready_count=0,
            recovered_count=1,
            manual_recovery_required_count=1,
            skipped_count=0,
            acted_count=1,
            candidates=[recovery_candidate],
        ),
        stale_buy_cancel_result=SimpleNamespace(
            trade_date=TRADE_DATE,
            scanned_at="2026-05-11T09:12:00+09:00",
            execute_cancels=True,
            unresolved_order_count=1,
            candidate_count=1,
            preview_ready_count=0,
            skipped_count=0,
            cancelled_count=1,
            rejected_count=0,
            unknown_count=0,
            blocked_count=0,
            acted_count=1,
            candidates=[cancel_candidate],
        ),
        stale_sell_cancel_result=SimpleNamespace(
            trade_date=TRADE_DATE,
            scanned_at="2026-05-11T09:13:00+09:00",
            execute_cancels=True,
            unresolved_order_count=0,
            candidate_count=0,
            preview_ready_count=0,
            skipped_count=0,
            cancelled_count=0,
            rejected_count=0,
            unknown_count=0,
            blocked_count=0,
            acted_count=0,
            candidates=[],
        ),
        stale_buy_signal_cleanup_result=SimpleNamespace(
            trade_date=TRADE_DATE,
            scanned_at="2026-05-11T09:14:00+09:00",
            execute_cleanup=True,
            matched_signal_count=1,
            candidate_count=1,
            preview_ready_count=1,
            skipped_count=0,
            cleaned_count=0,
            blocked_count=0,
            acted_count=0,
            audit_record_count=0,
            candidates=[signal_candidate],
        ),
        stale_sell_signal_cleanup_result=None,
    )


def _patch_runtime_dependencies(
    monkeypatch,
    *,
    test_db_path: Path,
    args: SimpleNamespace,
    connection: _FakeConnection,
    runtime_lock_service,
) -> None:
    monkeypatch.setattr(target, "_parse_args", lambda: args)
    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "run_migrations", lambda db_path: None)
    monkeypatch.setattr(
        target,
        "get_connection",
        lambda db_path, busy_timeout_ms=None: connection,
    )
    monkeypatch.setattr(target, "KisBroker", _FakeBroker)
    monkeypatch.setattr(target, "RuntimeLockService", runtime_lock_service)
    monkeypatch.setattr(target, "RuntimeLockRepository", lambda conn: object())
    monkeypatch.setattr(target, "OrderRepository", lambda conn: object())
    monkeypatch.setattr(target, "ExecutionRepository", lambda conn: object())
    monkeypatch.setattr(target, "PositionRepository", lambda conn: object())
    monkeypatch.setattr(target, "EntryLotRepository", lambda conn: object())
    monkeypatch.setattr(target, "SignalRepository", lambda conn: object())
    monkeypatch.setattr(target, "OrderService", _DummyService)
    monkeypatch.setattr(target, "UnresolvedOrderSyncService", _DummyService)
    monkeypatch.setattr(target, "ExecutionRecoveryFinalizeService", _DummyService)
    monkeypatch.setattr(target, "StaleBuyOrderCancelService", _DummyService)
    monkeypatch.setattr(target, "StaleSellOrderCancelService", _DummyService)
    monkeypatch.setattr(target, "StaleExecutionSignalCleanupService", _DummyService)


def test_resolve_lock_name_uses_explicit_value_when_present():
    args = SimpleNamespace(
        lock_name=" custom-lock ",
        trade_date=TRADE_DATE,
    )

    lock_name = target._resolve_lock_name(args)

    assert lock_name == "custom-lock"


def test_main_returns_lock_busy_and_writes_json_payload(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_run_order_maintenance_lock_busy.json"
    )
    args = _make_args(output_path, execute=True)
    connection = _FakeConnection()

    _BusyRuntimeLockService.instances.clear()
    _patch_runtime_dependencies(
        monkeypatch,
        test_db_path=test_db_path,
        args=args,
        connection=connection,
        runtime_lock_service=_BusyRuntimeLockService,
    )

    exit_code = target.main()

    assert exit_code == 4
    assert connection.closed is True
    assert len(_BusyRuntimeLockService.instances) == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["trade_date"] == TRADE_DATE
    assert payload["execute_mode"] is True
    assert payload["lock_name"] == f"order_maintenance:{TRADE_DATE}"
    assert payload["lock_owner_id"] == "busy-owner"
    assert payload["lock_acquired"] is False
    assert payload["lock_released"] is False
    assert payload["error_type"] == "RuntimeLockBusyError"
    assert "Runtime lock is already held by another process" in payload["error_message"]
    assert payload["result"] is None


def test_main_execute_success_writes_result_payload_and_releases_lock(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_run_order_maintenance_success.json"
    )
    args = _make_args(output_path, execute=True)
    connection = _FakeConnection()

    _SuccessfulRuntimeLockService.instances.clear()
    _FakeMaintenanceService.result = _make_maintenance_result()
    _patch_runtime_dependencies(
        monkeypatch,
        test_db_path=test_db_path,
        args=args,
        connection=connection,
        runtime_lock_service=_SuccessfulRuntimeLockService,
    )
    monkeypatch.setattr(target, "OrderMaintenanceService", _FakeMaintenanceService)

    exit_code = target.main()

    assert exit_code == 0
    assert connection.closed is True
    assert len(_SuccessfulRuntimeLockService.instances) == 1
    assert _SuccessfulRuntimeLockService.instances[0].acquired == [
        (f"order_maintenance:{TRADE_DATE}", 180)
    ]
    assert _SuccessfulRuntimeLockService.instances[0].released == [
        f"order_maintenance:{TRADE_DATE}"
    ]

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["trade_date"] == TRADE_DATE
    assert payload["execute_mode"] is True
    assert payload["lock_name"] == f"order_maintenance:{TRADE_DATE}"
    assert payload["lock_owner_id"] == "test-owner"
    assert payload["lock_acquired"] is False
    assert payload["lock_released"] is True
    assert payload["error_type"] is None
    assert payload["error_message"] is None
    assert payload["result"]["execute_changes"] is True
    assert payload["result"]["manual_recovery_required_client_order_ids"] == [
        "manual-001"
    ]
    assert payload["result"]["sync_result"]["candidate_count"] == 1
    assert (
        payload["result"]["execution_recovery_result"]["recovered_count"] == 1
    )
    assert (
        payload["result"]["stale_buy_cancel_result"]["cancelled_count"] == 1
    )
    assert (
        payload["result"]["stale_buy_signal_cleanup_result"]["preview_ready_count"]
        == 1
    )
    assert payload["result"]["stale_sell_signal_cleanup_result"] is None
