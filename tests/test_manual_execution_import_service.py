"""Tests for ManualExecutionImportService."""

from __future__ import annotations

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
    ENTRY_SLOT_TIMING2_MORNING,
    EntryLotRepository,
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
    SignalRepository,
)
from services import (
    ManualExecutionImportItem,
    ManualExecutionImportOutcome,
    ManualExecutionImportService,
    STRATEGY_NAME_SELL_EXECUTION_AUDIT,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
)


def _seed_submitted_order(
    conn,
    order_repo: OrderRepository,
    *,
    client_order_id: str,
    side: str = "buy",
    strategy_name: str = "seed",
):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol="005930",
            side=side,
            qty=2,
            price=71_000,
            order_type="LIMIT",
            strategy_name=strategy_name,
            requested_at="2026-04-17T09:01:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no="KIS-005930",
            submitted_at="2026-04-17T09:01:01+09:00",
        )


def _create_open_timing2_lot(
    conn,
    order_repo: OrderRepository,
    entry_lot_repo: EntryLotRepository,
    *,
    qty: int = 5,
    price: int = 10_000,
) -> int:
    with transaction(conn):
        order = order_repo.create(
            client_order_id=f"BUY_TIMING2_LOT_{qty}_{price}",
            symbol="005930",
            side="buy",
            qty=qty,
            price=0,
            order_type="MARKET",
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            requested_at="2026-04-17T09:00:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no="KIS-BUY-005930",
            submitted_at="2026-04-17T09:00:01+09:00",
        )
        lot = entry_lot_repo.apply_buy_execution(
            entry_order_id=order.id,
            symbol="005930",
            qty=qty,
            price=price,
            executed_at="2026-04-17T09:01:00+09:00",
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
    return lot.id


def _seed_submitted_lot_sell_order(
    conn,
    order_repo: OrderRepository,
    *,
    client_order_id: str,
    qty: int = 3,
) -> None:
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol="005930",
            side="sell",
            qty=qty,
            price=0,
            order_type="MARKET",
            strategy_name=STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
            requested_at="2026-04-17T10:10:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no=f"KIS-{client_order_id}",
            submitted_at="2026-04-17T10:10:01+09:00",
        )


def _record_sell_execution_audit(
    conn,
    signal_repo: SignalRepository,
    *,
    client_order_id: str,
    lot_id: int,
    sell_cost_rate: float = 0.002,
) -> None:
    with transaction(conn):
        row = signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            scanned_at="2026-04-17T10:10:02+09:00",
            payload={
                "trade_date": "2026-04-17",
                "source_signal_id": 123,
                "source_strategy_name": STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
                "symbol": "005930",
                "name": "Name-005930",
                "source_lot_id": lot_id,
                "requested_sell_qty": 3,
                "order_qty": 3,
                "sell_cost_rate": sell_cost_rate,
                "client_order_id": client_order_id,
                "execution_outcome": "SUBMITTED",
            },
        )
        signal_repo.mark_acted(row.id)


def test_execute_import_inserts_execution_and_updates_order_and_position(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        _seed_submitted_order(
            conn,
            order_repo,
            client_order_id="COID_IMPORT_OK",
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
        )

        result = service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id="COID_IMPORT_OK",
                    kis_exec_no="EXEC-1",
                    qty=2,
                    price=70_500,
                    executed_at="2026-04-17T09:05:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert result.imported_count == 1
        assert result.candidates[0].outcome == ManualExecutionImportOutcome.IMPORTED

        order_row = order_repo.get_by_client_order_id("COID_IMPORT_OK")
        assert order_row.status == DbOrderStatus.FILLED
        assert order_row.filled_qty == 2
        assert order_row.avg_fill_price == 70_500

        executions = execution_repo.list_by_order(order_row.id)
        assert len(executions) == 1
        assert executions[0].kis_exec_no == "EXEC-1"

        position = position_repo.get("005930")
        assert position is not None
        assert position.qty == 2
        assert position.avg_price == 70_500

        lots = entry_lot_repo.list_open_by_symbol(symbol="005930")
        assert len(lots) == 1
        assert lots[0].entry_order_id == order_row.id
        assert lots[0].entry_slot == ENTRY_SLOT_TIMING2_MORNING
        assert lots[0].total_buy_qty == 2
        assert lots[0].remaining_qty == 2
        assert lots[0].avg_buy_price == 70_500
    finally:
        conn.close()


def test_duplicate_exec_no_is_skipped_without_reapplying_position(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        _seed_submitted_order(conn, order_repo, client_order_id="COID_IMPORT_DUP")
        order_row = order_repo.get_by_client_order_id("COID_IMPORT_DUP")

        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="EXEC-DUP",
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:03:00+09:00",
            ) is True
            position_repo.apply_execution(
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:03:00+09:00",
            )
            order_repo.sync_execution_summary(
                client_order_id="COID_IMPORT_DUP",
                closed_at="2026-04-17T09:03:00+09:00",
            )

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=EntryLotRepository(conn),
        )

        result = service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id="COID_IMPORT_DUP",
                    kis_exec_no="EXEC-DUP",
                    qty=1,
                    price=70_000,
                    executed_at="2026-04-17T09:03:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert result.skipped_count == 1
        assert result.candidates[0].outcome == ManualExecutionImportOutcome.SKIPPED

        executions = execution_repo.list_by_order(order_row.id)
        assert len(executions) == 1
        position = position_repo.get("005930")
        assert position is not None
        assert position.qty == 1
        assert EntryLotRepository(conn).list_open_by_symbol(symbol="005930") == []
    finally:
        conn.close()


def test_execute_import_aggregates_split_buy_fills_into_one_entry_lot(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        _seed_submitted_order(
            conn,
            order_repo,
            client_order_id="COID_IMPORT_SPLIT",
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
        )

        result = service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id="COID_IMPORT_SPLIT",
                    kis_exec_no="EXEC-1",
                    qty=1,
                    price=70_000,
                    executed_at="2026-04-17T09:05:00+09:00",
                ),
                ManualExecutionImportItem(
                    client_order_id="COID_IMPORT_SPLIT",
                    kis_exec_no="EXEC-2",
                    qty=1,
                    price=71_000,
                    executed_at="2026-04-17T09:06:00+09:00",
                ),
            ],
            execute_import=True,
        )

        assert result.imported_count == 2
        order_row = order_repo.get_by_client_order_id("COID_IMPORT_SPLIT")
        assert order_row.status == DbOrderStatus.FILLED
        assert order_row.filled_qty == 2
        assert order_row.avg_fill_price == 70_500

        lots = entry_lot_repo.list_open_by_symbol(symbol="005930")
        assert len(lots) == 1
        assert lots[0].entry_order_id == order_row.id
        assert lots[0].total_buy_qty == 2
        assert lots[0].remaining_qty == 2
        assert lots[0].avg_buy_price == 70_500
    finally:
        conn.close()


def test_execute_import_reduces_timing2_lot_for_lot_level_sell(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        signal_repo = SignalRepository(conn)

        with transaction(conn):
            position_repo.upsert_from_broker(
                symbol="005930",
                qty=5,
                avg_price=10_000,
                updated_at="2026-04-17T09:01:00+09:00",
            )
        lot_id = _create_open_timing2_lot(
            conn,
            order_repo,
            entry_lot_repo,
            qty=5,
            price=10_000,
        )
        _seed_submitted_lot_sell_order(
            conn,
            order_repo,
            client_order_id="SELL_TIMING2_LOT_PARTIAL",
            qty=3,
        )
        _record_sell_execution_audit(
            conn,
            signal_repo,
            client_order_id="SELL_TIMING2_LOT_PARTIAL",
            lot_id=lot_id,
            sell_cost_rate=0.002,
        )

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
        )

        result = service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id="SELL_TIMING2_LOT_PARTIAL",
                    kis_exec_no="SELL-EXEC-1",
                    qty=3,
                    price=11_000,
                    executed_at="2026-04-17T10:11:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert result.imported_count == 1
        assert result.candidates[0].outcome == ManualExecutionImportOutcome.IMPORTED

        sell_order = order_repo.get_by_client_order_id("SELL_TIMING2_LOT_PARTIAL")
        assert sell_order.status == DbOrderStatus.FILLED
        assert sell_order.filled_qty == 3

        position = position_repo.get("005930")
        assert position is not None
        assert position.qty == 2

        lot = entry_lot_repo.get(lot_id)
        assert lot is not None
        assert lot.remaining_qty == 2
        assert lot.realized_sell_qty == 3
        assert lot.realized_pnl == 2_934
        assert lot.status == "OPEN"
    finally:
        conn.close()


def test_execute_import_blocks_lot_level_sell_when_audit_is_missing(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        signal_repo = SignalRepository(conn)

        with transaction(conn):
            position_repo.upsert_from_broker(
                symbol="005930",
                qty=5,
                avg_price=10_000,
                updated_at="2026-04-17T09:01:00+09:00",
            )
        lot_id = _create_open_timing2_lot(
            conn,
            order_repo,
            entry_lot_repo,
            qty=5,
            price=10_000,
        )
        _seed_submitted_lot_sell_order(
            conn,
            order_repo,
            client_order_id="SELL_TIMING2_LOT_NO_AUDIT",
            qty=3,
        )

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
        )

        result = service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id="SELL_TIMING2_LOT_NO_AUDIT",
                    kis_exec_no="SELL-EXEC-1",
                    qty=3,
                    price=11_000,
                    executed_at="2026-04-17T10:11:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert result.blocked_count == 1
        assert result.candidates[0].reason_code == "LOT_SELL_AUDIT_NOT_FOUND"

        sell_order = order_repo.get_by_client_order_id("SELL_TIMING2_LOT_NO_AUDIT")
        assert sell_order.status == DbOrderStatus.SUBMITTED
        assert execution_repo.list_by_order(sell_order.id) == []

        position = position_repo.get("005930")
        assert position is not None
        assert position.qty == 5
        lot = entry_lot_repo.get(lot_id)
        assert lot is not None
        assert lot.remaining_qty == 5
    finally:
        conn.close()
