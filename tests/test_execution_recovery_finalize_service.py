"""Tests for ExecutionRecoveryFinalizeService."""

from __future__ import annotations

from datetime import datetime

import pytz

from services import (
    ExecutionRecoveryFinalizeOutcome,
    ExecutionRecoveryFinalizeService,
    STRATEGY_NAME_SELL_EXECUTION_AUDIT,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
    UnresolvedOrderSyncAction,
    UnresolvedOrderSyncCandidate,
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncResult,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
    EntryLotRepository,
    ExecutionRepository,
    OrderRepository,
    SignalRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


class _FakeSyncService:
    def __init__(self, result: UnresolvedOrderSyncResult) -> None:
        self.result = result

    def sync_unresolved_orders(self, *, trade_date: str, execute_sync: bool):
        assert trade_date == TRADE_DATE
        assert execute_sync is False
        return self.result


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 17, 10, 15, 0))


def _seed_submitted_order(
    conn,
    order_repo: OrderRepository,
    *,
    client_order_id: str,
    strategy_name: str = "seed",
    side: str = "buy",
    qty: int = 2,
    price: int = 71_000,
    order_type: str = "LIMIT",
):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol="005930",
            side=side,
            qty=qty,
            price=price,
            order_type=order_type,
            strategy_name=strategy_name,
            requested_at="2026-04-17T09:05:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no="KIS-005930",
            submitted_at="2026-04-17T09:05:01+09:00",
        )


def _sync_result(
    client_order_id: str,
    *,
    broker_filled_qty: int = 2,
    broker_status: str = "filled",
) -> UnresolvedOrderSyncResult:
    return UnresolvedOrderSyncResult(
        trade_date=TRADE_DATE,
        scanned_at="2026-04-17T10:10:00+09:00",
        execute_sync=False,
        unresolved_order_count=1,
        candidate_count=1,
        preview_ready_count=0,
        skipped_count=0,
        synced_count=0,
        execution_recovery_required_count=1,
        acted_count=0,
        candidates=(
            UnresolvedOrderSyncCandidate(
                client_order_id=client_order_id,
                symbol="005930",
                status_before="SUBMITTED",
                status_after=None,
                kis_order_no="KIS-005930",
                action=UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED,
                outcome=UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED,
                reason_code="EXECUTION_RECOVERY_REQUIRED",
                reason_message="recover first",
                broker_status=broker_status,
                broker_filled_qty=broker_filled_qty,
                acted=False,
            ),
        ),
    )


def _create_timing2_entry_lot(
    conn,
    order_repo: OrderRepository,
    entry_lot_repo: EntryLotRepository,
    *,
    client_order_id: str = "COID_TIMING2_BUY_FOR_SELL",
    qty: int = 5,
    price: int = 10_000,
) -> int:
    _seed_submitted_order(
        conn,
        order_repo,
        client_order_id=client_order_id,
        strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        side="buy",
        qty=qty,
        price=0,
        order_type="MARKET",
    )
    order_row = order_repo.get_by_client_order_id(client_order_id)
    with transaction(conn):
        lot = entry_lot_repo.apply_buy_execution(
            entry_order_id=order_row.id,
            symbol="005930",
            qty=qty,
            price=price,
            executed_at="2026-04-17T09:06:00+09:00",
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
    return lot.id


def _seed_submitted_timing2_lot_sell_order(
    conn,
    order_repo: OrderRepository,
    *,
    client_order_id: str,
    qty: int = 3,
):
    _seed_submitted_order(
        conn,
        order_repo,
        client_order_id=client_order_id,
        strategy_name=STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
        side="sell",
        qty=qty,
        price=0,
        order_type="MARKET",
    )


def _record_lot_sell_audit(
    conn,
    signal_repo: SignalRepository,
    *,
    client_order_id: str,
    lot_id: int,
    order_qty: int = 3,
    remaining_qty_before: int = 5,
    realized_sell_qty_before: int = 0,
) -> None:
    with transaction(conn):
        row = signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            scanned_at="2026-04-17T10:07:00+09:00",
            payload={
                "trade_date": TRADE_DATE,
                "source_signal_id": 123,
                "source_strategy_name": (
                    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL
                ),
                "symbol": "005930",
                "name": "Samsung",
                "source_lot_id": lot_id,
                "requested_sell_qty": order_qty,
                "order_qty": order_qty,
                "sell_cost_rate": 0.002140527,
                "execution_outcome": "SUBMITTED",
                "client_order_id": client_order_id,
                "source_lot_remaining_qty_before": remaining_qty_before,
                "source_lot_realized_sell_qty_before": realized_sell_qty_before,
                "source_lot_status_before": "OPEN",
            },
        )
        signal_repo.mark_acted(row.id)


def test_execute_finalizes_filled_order_from_local_execution_rows(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        _seed_submitted_order(conn, order_repo, client_order_id="COID_RECOVER_OK")
        order_row = order_repo.get_by_client_order_id("COID_RECOVER_OK")

        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E1",
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:06:00+09:00",
            ) is True
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E2",
                symbol="005930",
                side="buy",
                qty=1,
                price=71_000,
                executed_at="2026-04-17T09:07:00+09:00",
            ) is True

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(_sync_result("COID_RECOVER_OK")),
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=True,
        )

        assert result.recovered_count == 1
        assert result.candidates[0].outcome == ExecutionRecoveryFinalizeOutcome.RECOVERED
        updated = order_repo.get_by_client_order_id("COID_RECOVER_OK")
        assert updated.status == DbOrderStatus.FILLED
        assert updated.filled_qty == 2
        assert updated.avg_fill_price == 70_500
    finally:
        conn.close()


def test_missing_local_execution_rows_stays_manual_recovery_required(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        _seed_submitted_order(conn, order_repo, client_order_id="COID_RECOVER_MANUAL")

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(_sync_result("COID_RECOVER_MANUAL")),
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=False,
        )

        assert result.manual_recovery_required_count == 1
        assert (
            result.candidates[0].outcome
            == ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED
        )
        assert result.candidates[0].reason_code == "LOCAL_EXECUTIONS_MISSING"
        assert (
            order_repo.get_by_client_order_id("COID_RECOVER_MANUAL").status
            == DbOrderStatus.SUBMITTED
        )
    finally:
        conn.close()


def test_timing2_buy_recovery_requires_matching_entry_lot(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        _seed_submitted_order(
            conn,
            order_repo,
            client_order_id="COID_TIMING2_LOT_MISSING",
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
        order_row = order_repo.get_by_client_order_id("COID_TIMING2_LOT_MISSING")

        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E1",
                symbol="005930",
                side="buy",
                qty=2,
                price=70_500,
                executed_at="2026-04-17T09:06:00+09:00",
            ) is True

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(_sync_result("COID_TIMING2_LOT_MISSING")),
            entry_lot_repo=entry_lot_repo,
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=True,
        )

        assert result.manual_recovery_required_count == 1
        assert (
            result.candidates[0].outcome
            == ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED
        )
        assert result.candidates[0].reason_code == "TIMING2_ENTRY_LOT_MISSING"
        unchanged = order_repo.get_by_client_order_id("COID_TIMING2_LOT_MISSING")
        assert unchanged.status == DbOrderStatus.SUBMITTED
        assert unchanged.filled_qty == 0
    finally:
        conn.close()


def test_timing2_buy_recovery_allows_matching_entry_lot(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        _seed_submitted_order(
            conn,
            order_repo,
            client_order_id="COID_TIMING2_LOT_OK",
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
        order_row = order_repo.get_by_client_order_id("COID_TIMING2_LOT_OK")

        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E1",
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:06:00+09:00",
            ) is True
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E2",
                symbol="005930",
                side="buy",
                qty=1,
                price=71_000,
                executed_at="2026-04-17T09:07:00+09:00",
            ) is True
            entry_lot_repo.apply_buy_execution(
                entry_order_id=order_row.id,
                symbol="005930",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:06:00+09:00",
                entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            )
            entry_lot_repo.apply_buy_execution(
                entry_order_id=order_row.id,
                symbol="005930",
                qty=1,
                price=71_000,
                executed_at="2026-04-17T09:07:00+09:00",
                entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            )

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(_sync_result("COID_TIMING2_LOT_OK")),
            entry_lot_repo=entry_lot_repo,
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=True,
        )

        assert result.recovered_count == 1
        assert result.candidates[0].outcome == ExecutionRecoveryFinalizeOutcome.RECOVERED
        updated = order_repo.get_by_client_order_id("COID_TIMING2_LOT_OK")
        assert updated.status == DbOrderStatus.FILLED
        assert updated.filled_qty == 2
        assert updated.avg_fill_price == 70_500
    finally:
        conn.close()


def test_timing2_lot_sell_recovery_requires_lot_reduction(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        signal_repo = SignalRepository(conn)
        lot_id = _create_timing2_entry_lot(
            conn,
            order_repo,
            entry_lot_repo,
        )
        _seed_submitted_timing2_lot_sell_order(
            conn,
            order_repo,
            client_order_id="COID_TIMING2_SELL_LOT_NOT_REDUCED",
            qty=3,
        )
        order_row = order_repo.get_by_client_order_id(
            "COID_TIMING2_SELL_LOT_NOT_REDUCED"
        )
        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="S1",
                symbol="005930",
                side="sell",
                qty=3,
                price=11_000,
                executed_at="2026-04-17T10:10:00+09:00",
            ) is True
        _record_lot_sell_audit(
            conn,
            signal_repo,
            client_order_id="COID_TIMING2_SELL_LOT_NOT_REDUCED",
            lot_id=lot_id,
        )

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(
                _sync_result(
                    "COID_TIMING2_SELL_LOT_NOT_REDUCED",
                    broker_filled_qty=3,
                )
            ),
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=True,
        )

        assert result.manual_recovery_required_count == 1
        assert (
            result.candidates[0].outcome
            == ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED
        )
        assert (
            result.candidates[0].reason_code
            == "TIMING2_SELL_ENTRY_LOT_REMAINING_MISMATCH"
        )
        unchanged = order_repo.get_by_client_order_id(
            "COID_TIMING2_SELL_LOT_NOT_REDUCED"
        )
        assert unchanged.status == DbOrderStatus.SUBMITTED
        assert unchanged.filled_qty == 0
    finally:
        conn.close()


def test_timing2_lot_sell_recovery_allows_already_reduced_lot(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        signal_repo = SignalRepository(conn)
        lot_id = _create_timing2_entry_lot(
            conn,
            order_repo,
            entry_lot_repo,
        )
        _seed_submitted_timing2_lot_sell_order(
            conn,
            order_repo,
            client_order_id="COID_TIMING2_SELL_LOT_OK",
            qty=3,
        )
        order_row = order_repo.get_by_client_order_id("COID_TIMING2_SELL_LOT_OK")
        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="S1",
                symbol="005930",
                side="sell",
                qty=3,
                price=11_000,
                executed_at="2026-04-17T10:10:00+09:00",
            ) is True
            entry_lot_repo.apply_sell_to_lot(
                lot_id=lot_id,
                qty=3,
                price=11_000,
                executed_at="2026-04-17T10:10:00+09:00",
                sell_cost_rate=0.002140527,
            )
        _record_lot_sell_audit(
            conn,
            signal_repo,
            client_order_id="COID_TIMING2_SELL_LOT_OK",
            lot_id=lot_id,
        )

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(
                _sync_result(
                    "COID_TIMING2_SELL_LOT_OK",
                    broker_filled_qty=3,
                )
            ),
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=True,
        )

        assert result.recovered_count == 1
        assert result.candidates[0].outcome == ExecutionRecoveryFinalizeOutcome.RECOVERED
        updated = order_repo.get_by_client_order_id("COID_TIMING2_SELL_LOT_OK")
        assert updated.status == DbOrderStatus.FILLED
        assert updated.filled_qty == 3
        assert updated.avg_fill_price == 11_000
    finally:
        conn.close()
