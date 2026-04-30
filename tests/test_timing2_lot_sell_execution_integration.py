"""Integration test for Timing2 lot exit scan -> sell execution flow."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import (
    ManualExecutionImportItem,
    ManualExecutionImportOutcome,
    ManualExecutionImportService,
    OrderOutcome,
    OrderService,
    SellSignalExecutionOutcome,
    SellSignalExecutionService,
    SellSignalExecutionSettings,
    STRATEGY_NAME_SELL_EXECUTION_AUDIT,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
    STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK,
    STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
    Timing2LotExitScanService,
    TradingRiskGuardService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    DbOrderStatus,
    ENTRY_SLOT_TIMING2_RANGE,
    EntryLotRepository,
    ExecutionRepository,
    IntradayBar30s,
    IntradayBar30sRepository,
    OrderRepository,
    PositionRepository,
    SignalRepository,
    TradingControlRepository,
)
from strategy import Timing2LotExitSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


class _FakeBroker(BrokerInterface):
    def __init__(self, price_map: dict[str, PriceSnapshot]) -> None:
        self._price_map = price_map

    def get_access_token(self) -> str:
        raise NotImplementedError

    def get_current_price(self, code: str) -> PriceSnapshot:
        return self._price_map[code]

    def get_daily_candles(self, code: str, count: int = 30, end_date: str | None = None):
        raise NotImplementedError

    def get_minute_candles(self, code: str, interval: str = "1"):
        raise NotImplementedError

    def get_balance(self) -> Balance:
        raise NotImplementedError

    def place_order(self, code: str, side: str, quantity: int, price: int = 0) -> OrderInfo:
        raise NotImplementedError

    def cancel_order(self, order_no: str, code: str, quantity: int) -> OrderInfo:
        raise NotImplementedError

    def get_order_status(self, order_no: str | None = None, *, filled_only: bool = False):
        raise NotImplementedError


class _PersistingSellOrderService:
    def __init__(self, conn, order_repo: OrderRepository) -> None:
        self._conn = conn
        self._order_repo = order_repo
        self.calls: list[dict[str, object]] = []

    def place_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        price: int,
        order_type: str,
        strategy_name: str | None,
    ):
        self.calls.append(
            {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "order_type": order_type,
                "strategy_name": strategy_name,
            }
        )
        client_order_id = f"20260417101005-{side}-{symbol}-{len(self.calls)}"
        with transaction(self._conn):
            self._order_repo.create(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                order_type=order_type,
                strategy_name=strategy_name,
                requested_at="2026-04-17T10:10:05+09:00",
            )
            self._order_repo.mark_submitted(
                client_order_id=client_order_id,
                kis_order_no=f"KIS-{client_order_id}",
                submitted_at="2026-04-17T10:10:06+09:00",
            )
        return SimpleNamespace(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id=client_order_id,
            error_code=None,
            error_message=None,
        )


def _kst_datetime(hour: int, minute: int, second: int = 0) -> datetime:
    return KST.localize(datetime(2026, 4, 17, hour, minute, second))


def _make_price_snapshot(symbol: str, price: int) -> PriceSnapshot:
    return PriceSnapshot(
        code=symbol,
        name=f"Name-{symbol}",
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=price,
        change=0,
        change_rate=0.0,
        volume=1,
        timestamp=_kst_datetime(10, 10, 0),
    )


def _seed_open_timing2_lot(
    conn,
    *,
    strategy_name: str,
    client_order_id: str,
    qty: int,
    price: int,
    executed_at: str,
) -> int:
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)
    position_repo = PositionRepository(conn)
    entry_lot_repo = EntryLotRepository(conn)

    with transaction(conn):
        order = order_repo.create(
            client_order_id=client_order_id,
            symbol="005930",
            side="buy",
            qty=qty,
            price=0,
            order_type="MARKET",
            strategy_name=strategy_name,
            requested_at=executed_at,
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no=f"KIS-{client_order_id}",
            submitted_at=executed_at,
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no=f"EXEC-{client_order_id}",
            symbol="005930",
            side="buy",
            qty=qty,
            price=price,
            executed_at=executed_at,
        )
        order_repo.mark_filled(
            client_order_id=order.client_order_id,
            closed_at=executed_at,
        )
        position_repo.apply_execution(
            symbol="005930",
            side="buy",
            qty=qty,
            price=price,
            executed_at=executed_at,
        )
        lot = entry_lot_repo.apply_buy_execution(
            entry_order_id=order.id,
            symbol="005930",
            qty=qty,
            price=price,
            executed_at=executed_at,
            entry_strategy_name=strategy_name,
        )
    return lot.id


def _seed_existing_partial_sell_on_lot(
    conn,
    *,
    lot_id: int,
    qty: int,
    price: int,
    executed_at: str,
) -> None:
    entry_lot_repo = EntryLotRepository(conn)
    position_repo = PositionRepository(conn)
    with transaction(conn):
        entry_lot_repo.apply_sell_to_lot(
            lot_id=lot_id,
            qty=qty,
            price=price,
            executed_at=executed_at,
            sell_cost_rate=0.0,
        )
        position_repo.apply_execution(
            symbol="005930",
            side="sell",
            qty=qty,
            price=price,
            executed_at=executed_at,
        )


def _store_complete_3m_closes(conn, *, symbol: str, closes: list[int]) -> None:
    repo = IntradayBar30sRepository(conn)
    bars: list[IntradayBar30s] = []
    session_start = KST.localize(datetime(2026, 4, 17, 9, 0, 0))

    for bucket_index, close in enumerate(closes):
        bucket_start = session_start + timedelta(minutes=3 * bucket_index)
        for offset in range(6):
            bar_start = bucket_start + timedelta(seconds=30 * offset)
            bar_end = bar_start + timedelta(seconds=30)
            bar_close = close if offset == 5 else close + 10
            bars.append(
                IntradayBar30s(
                    bar_start_at=bar_start.isoformat(),
                    bar_end_at=bar_end.isoformat(),
                    open=bar_close,
                    high=bar_close,
                    low=bar_close,
                    close=bar_close,
                    volume=10,
                )
            )

    with transaction(conn):
        repo.upsert_many_for_symbol_and_date(
            trade_date=TRADE_DATE,
            symbol=symbol,
            bars=bars,
            refreshed_at="2026-04-17T10:30:00+09:00",
        )


def test_timing2_lot_stop_loss_signal_flows_into_range_only_sell_execution_audit(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)

        morning_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            client_order_id="BUY-T2-MORNING-005930",
            qty=3,
            price=10_800,
            executed_at="2026-04-17T09:01:00+09:00",
        )
        range_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            client_order_id="BUY-T2-RANGE-005930",
            qty=2,
            price=11_000,
            executed_at="2026-04-17T10:00:30+09:00",
        )

        scan_service = Timing2LotExitScanService(
            broker=_FakeBroker(
                {"005930": _make_price_snapshot(symbol="005930", price=10_850)}
            ),
            conn=conn,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 10, 0),
        )

        scan_result = scan_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2LotExitSettings(),
            write_signals=True,
        )

        assert scan_result.lot_count == 2
        assert scan_result.matched_count == 1
        assert scan_result.stop_loss_count == 1
        assert scan_result.recorded_count == 1
        assert scan_result.candidates[0].lot_id == range_lot_id
        assert scan_result.candidates[0].strategy_name == STRATEGY_NAME_TIMING2_LOT_STOP_LOSS
        assert scan_result.candidates[0].remaining_qty == 2

        recorded_signal = scan_result.recorded_signals[0]
        assert recorded_signal.payload is not None
        assert recorded_signal.payload["lot_id"] == range_lot_id
        assert recorded_signal.payload["sell_qty"] == 2
        assert recorded_signal.payload["entry_slot"] == ENTRY_SLOT_TIMING2_RANGE

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot(
            symbol="005930",
            price=10_850,
        )

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = SimpleNamespace(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id="20260417101000-sell-005930-range",
            error_code=None,
            error_message=None,
        )

        execution_service = SellSignalExecutionService(
            broker=broker,
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            order_service=order_service,
            entry_lot_repo=entry_lot_repo,
            risk_guard_service=TradingRiskGuardService(
                order_repo=order_repo,
                trading_control_repo=TradingControlRepository(conn),
                daily_stats_repo=DailyStatsRepository(conn),
                now_fn=lambda: _kst_datetime(10, 10, 5),
            ),
            now_fn=lambda: _kst_datetime(10, 10, 5),
        )

        execution_result = execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=SellSignalExecutionSettings(),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 1
        assert execution_result.submitted_count == 1
        assert execution_result.acted_count == 1
        assert execution_result.audit_record_count == 1
        assert execution_result.candidates[0].symbol == "005930"
        assert execution_result.candidates[0].lot_id == range_lot_id
        assert execution_result.candidates[0].requested_sell_qty == 2
        assert execution_result.candidates[0].order_qty == 2
        assert execution_result.candidates[0].source_strategy_name == STRATEGY_NAME_TIMING2_LOT_STOP_LOSS
        assert signal_repo.get(recorded_signal.id).acted is True

        order_service.place_order.assert_called_once()
        _, kwargs = order_service.place_order.call_args
        assert kwargs["side"] == "sell"
        assert kwargs["qty"] == 2
        assert kwargs["strategy_name"] == STRATEGY_NAME_TIMING2_LOT_STOP_LOSS

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == recorded_signal.id
        assert audit_rows[0].payload["source_lot_id"] == range_lot_id
        assert audit_rows[0].payload["requested_sell_qty"] == 2
        assert audit_rows[0].payload["order_qty"] == 2
        assert audit_rows[0].payload["source_strategy_name"] == STRATEGY_NAME_TIMING2_LOT_STOP_LOSS
        assert audit_rows[0].payload["source_lot_remaining_qty_before"] == 2
        assert audit_rows[0].payload["source_lot_realized_sell_qty_before"] == 0
        assert audit_rows[0].payload["source_lot_status_before"] == "OPEN"

        morning_lot = entry_lot_repo.get(morning_lot_id)
        assert morning_lot is not None
        assert morning_lot.remaining_qty == 3
        assert morning_lot.realized_sell_qty == 0
        assert morning_lot.status == "OPEN"

        range_lot = entry_lot_repo.get(range_lot_id)
        assert range_lot is not None
        assert range_lot.remaining_qty == 2
        assert range_lot.realized_sell_qty == 0
        assert range_lot.status == "OPEN"

        live_position = position_repo.get("005930")
        assert live_position is not None
        assert live_position.qty == 5
    finally:
        conn.close()


def test_timing2_lot_stop_loss_sell_flow_reduces_only_range_lot_after_manual_import(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)

        morning_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            client_order_id="BUY-T2-MORNING-REDUCE-005930",
            qty=3,
            price=10_800,
            executed_at="2026-04-17T09:01:00+09:00",
        )
        range_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            client_order_id="BUY-T2-RANGE-REDUCE-005930",
            qty=2,
            price=11_000,
            executed_at="2026-04-17T10:00:30+09:00",
        )

        scan_service = Timing2LotExitScanService(
            broker=_FakeBroker(
                {"005930": _make_price_snapshot(symbol="005930", price=10_850)}
            ),
            conn=conn,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 10, 0),
        )

        scan_result = scan_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2LotExitSettings(),
            write_signals=True,
        )
        recorded_signal = scan_result.recorded_signals[0]

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot(
            symbol="005930",
            price=10_850,
        )
        order_service = _PersistingSellOrderService(conn, order_repo)

        execution_service = SellSignalExecutionService(
            broker=broker,
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            order_service=order_service,
            entry_lot_repo=entry_lot_repo,
            risk_guard_service=TradingRiskGuardService(
                order_repo=order_repo,
                trading_control_repo=TradingControlRepository(conn),
                daily_stats_repo=DailyStatsRepository(conn),
                now_fn=lambda: _kst_datetime(10, 10, 5),
            ),
            now_fn=lambda: _kst_datetime(10, 10, 5),
        )

        execution_result = execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=SellSignalExecutionSettings(),
            execute_orders=True,
        )

        assert execution_result.submitted_count == 1
        assert execution_result.audit_record_count == 1
        assert len(order_service.calls) == 1
        client_order_id = execution_result.candidates[0].client_order_id
        assert client_order_id is not None

        submitted_order = order_repo.get_by_client_order_id(client_order_id)
        assert submitted_order is not None
        assert submitted_order.status == DbOrderStatus.SUBMITTED
        assert submitted_order.filled_qty == 0

        import_service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
        )

        import_result = import_service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id=client_order_id,
                    kis_exec_no="SELL-RANGE-END-TO-END-1",
                    qty=2,
                    price=10_850,
                    executed_at="2026-04-17T10:11:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert import_result.imported_count == 1
        assert (
            import_result.candidates[0].outcome
            == ManualExecutionImportOutcome.IMPORTED
        )

        imported_order = order_repo.get_by_client_order_id(client_order_id)
        assert imported_order is not None
        assert imported_order.status == DbOrderStatus.FILLED
        assert imported_order.filled_qty == 2

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == recorded_signal.id
        assert audit_rows[0].payload["source_lot_id"] == range_lot_id
        assert audit_rows[0].payload["client_order_id"] == client_order_id

        morning_lot = entry_lot_repo.get(morning_lot_id)
        assert morning_lot is not None
        assert morning_lot.remaining_qty == 3
        assert morning_lot.realized_sell_qty == 0
        assert morning_lot.status == "OPEN"

        range_lot = entry_lot_repo.get(range_lot_id)
        assert range_lot is not None
        assert range_lot.remaining_qty == 0
        assert range_lot.realized_sell_qty == 2
        assert range_lot.status == "CLOSED"

        live_position = position_repo.get("005930")
        assert live_position is not None
        assert live_position.qty == 3
    finally:
        conn.close()


def test_timing2_lot_partial_take_profit_flow_reduces_only_range_lot_and_keeps_it_open(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)

        morning_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            client_order_id="BUY-T2-MORNING-PARTIAL-005930",
            qty=3,
            price=10_000,
            executed_at="2026-04-17T09:01:00+09:00",
        )
        _seed_existing_partial_sell_on_lot(
            conn,
            lot_id=morning_lot_id,
            qty=1,
            price=10_500,
            executed_at="2026-04-17T09:30:00+09:00",
        )
        range_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            client_order_id="BUY-T2-RANGE-PARTIAL-005930",
            qty=5,
            price=11_000,
            executed_at="2026-04-17T10:00:30+09:00",
        )

        scan_service = Timing2LotExitScanService(
            broker=_FakeBroker(
                {"005930": _make_price_snapshot(symbol="005930", price=11_550)}
            ),
            conn=conn,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 20, 0),
        )

        scan_result = scan_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2LotExitSettings(),
            write_signals=True,
        )

        assert scan_result.lot_count == 2
        assert scan_result.matched_count == 1
        assert scan_result.partial_take_profit_count == 1
        assert scan_result.recorded_count == 1
        assert scan_result.candidates[0].lot_id == range_lot_id
        assert (
            scan_result.candidates[0].strategy_name
            == STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL
        )
        assert scan_result.candidates[0].remaining_qty == 5

        recorded_signal = scan_result.recorded_signals[0]
        assert recorded_signal.payload is not None
        assert recorded_signal.payload["lot_id"] == range_lot_id
        assert recorded_signal.payload["sell_qty"] == 3
        assert recorded_signal.payload["entry_slot"] == ENTRY_SLOT_TIMING2_RANGE

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot(
            symbol="005930",
            price=11_550,
        )
        order_service = _PersistingSellOrderService(conn, order_repo)

        execution_service = SellSignalExecutionService(
            broker=broker,
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            order_service=order_service,
            entry_lot_repo=entry_lot_repo,
            risk_guard_service=TradingRiskGuardService(
                order_repo=order_repo,
                trading_control_repo=TradingControlRepository(conn),
                daily_stats_repo=DailyStatsRepository(conn),
                now_fn=lambda: _kst_datetime(10, 20, 5),
            ),
            now_fn=lambda: _kst_datetime(10, 20, 5),
        )

        execution_result = execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=SellSignalExecutionSettings(),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 1
        assert execution_result.submitted_count == 1
        assert execution_result.acted_count == 1
        assert execution_result.audit_record_count == 1
        assert execution_result.candidates[0].lot_id == range_lot_id
        assert execution_result.candidates[0].requested_sell_qty == 3
        assert execution_result.candidates[0].order_qty == 3
        assert (
            execution_result.candidates[0].source_strategy_name
            == STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL
        )
        client_order_id = execution_result.candidates[0].client_order_id
        assert client_order_id is not None
        assert signal_repo.get(recorded_signal.id).acted is True

        import_service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
        )

        import_result = import_service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id=client_order_id,
                    kis_exec_no="SELL-RANGE-PARTIAL-END-TO-END-1",
                    qty=3,
                    price=11_550,
                    executed_at="2026-04-17T10:21:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert import_result.imported_count == 1
        assert (
            import_result.candidates[0].outcome
            == ManualExecutionImportOutcome.IMPORTED
        )

        imported_order = order_repo.get_by_client_order_id(client_order_id)
        assert imported_order is not None
        assert imported_order.status == DbOrderStatus.FILLED
        assert imported_order.filled_qty == 3

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == recorded_signal.id
        assert audit_rows[0].payload["source_lot_id"] == range_lot_id
        assert audit_rows[0].payload["requested_sell_qty"] == 3
        assert audit_rows[0].payload["order_qty"] == 3
        assert audit_rows[0].payload["client_order_id"] == client_order_id
        assert audit_rows[0].payload["source_lot_remaining_qty_before"] == 5
        assert audit_rows[0].payload["source_lot_realized_sell_qty_before"] == 0

        morning_lot = entry_lot_repo.get(morning_lot_id)
        assert morning_lot is not None
        assert morning_lot.remaining_qty == 2
        assert morning_lot.realized_sell_qty == 1
        assert morning_lot.status == "OPEN"

        range_lot = entry_lot_repo.get(range_lot_id)
        assert range_lot is not None
        assert range_lot.remaining_qty == 2
        assert range_lot.realized_sell_qty == 3
        assert range_lot.status == "OPEN"

        live_position = position_repo.get("005930")
        assert live_position is not None
        assert live_position.qty == 4
    finally:
        conn.close()


def test_timing2_lot_ma_break_flow_closes_range_lot_end_to_end(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)

        range_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            client_order_id="BUY-T2-RANGE-MA-BREAK-005930",
            qty=5,
            price=10_000,
            executed_at="2026-04-17T10:00:30+09:00",
        )
        _store_complete_3m_closes(
            conn,
            symbol="005930",
            closes=[10_500, 10_400, 10_300, 10_200, 10_000],
        )

        scan_service = Timing2LotExitScanService(
            broker=_FakeBroker(
                {"005930": _make_price_snapshot(symbol="005930", price=10_600)}
            ),
            conn=conn,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 30, 0),
        )

        scan_result = scan_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2LotExitSettings(),
            write_signals=True,
        )

        assert scan_result.lot_count == 1
        assert scan_result.matched_count == 1
        assert scan_result.ma_break_count == 1
        assert scan_result.partial_take_profit_count == 0
        assert scan_result.recorded_count == 1
        assert scan_result.candidates[0].lot_id == range_lot_id
        assert (
            scan_result.candidates[0].strategy_name
            == STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK
        )
        assert scan_result.candidates[0].remaining_qty == 5

        recorded_signal = scan_result.recorded_signals[0]
        assert recorded_signal.payload is not None
        assert recorded_signal.payload["lot_id"] == range_lot_id
        assert recorded_signal.payload["sell_qty"] == 5
        assert recorded_signal.payload["entry_slot"] == ENTRY_SLOT_TIMING2_RANGE
        assert recorded_signal.payload["latest_3m_close"] == 10_000
        assert recorded_signal.payload["ma5_3m"] == 10_280

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot(
            symbol="005930",
            price=10_600,
        )
        order_service = _PersistingSellOrderService(conn, order_repo)

        execution_service = SellSignalExecutionService(
            broker=broker,
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            order_service=order_service,
            entry_lot_repo=entry_lot_repo,
            risk_guard_service=TradingRiskGuardService(
                order_repo=order_repo,
                trading_control_repo=TradingControlRepository(conn),
                daily_stats_repo=DailyStatsRepository(conn),
                now_fn=lambda: _kst_datetime(10, 30, 5),
            ),
            now_fn=lambda: _kst_datetime(10, 30, 5),
        )

        execution_result = execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=SellSignalExecutionSettings(),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 1
        assert execution_result.submitted_count == 1
        assert execution_result.acted_count == 1
        assert execution_result.audit_record_count == 1
        assert execution_result.candidates[0].lot_id == range_lot_id
        assert execution_result.candidates[0].requested_sell_qty == 5
        assert execution_result.candidates[0].order_qty == 5
        assert (
            execution_result.candidates[0].source_strategy_name
            == STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK
        )
        client_order_id = execution_result.candidates[0].client_order_id
        assert client_order_id is not None
        assert signal_repo.get(recorded_signal.id).acted is True

        import_service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
        )

        import_result = import_service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id=client_order_id,
                    kis_exec_no="SELL-RANGE-MA-BREAK-END-TO-END-1",
                    qty=5,
                    price=10_600,
                    executed_at="2026-04-17T10:31:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert import_result.imported_count == 1
        assert (
            import_result.candidates[0].outcome
            == ManualExecutionImportOutcome.IMPORTED
        )

        imported_order = order_repo.get_by_client_order_id(client_order_id)
        assert imported_order is not None
        assert imported_order.status == DbOrderStatus.FILLED
        assert imported_order.filled_qty == 5

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == recorded_signal.id
        assert audit_rows[0].payload["source_lot_id"] == range_lot_id
        assert audit_rows[0].payload["requested_sell_qty"] == 5
        assert audit_rows[0].payload["order_qty"] == 5
        assert audit_rows[0].payload["client_order_id"] == client_order_id
        assert audit_rows[0].payload["source_lot_remaining_qty_before"] == 5
        assert audit_rows[0].payload["source_lot_realized_sell_qty_before"] == 0

        range_lot = entry_lot_repo.get(range_lot_id)
        assert range_lot is not None
        assert range_lot.remaining_qty == 0
        assert range_lot.realized_sell_qty == 5
        assert range_lot.status == "CLOSED"

        live_position = position_repo.get("005930")
        assert live_position is not None
        assert live_position.qty == 0
    finally:
        conn.close()


def test_timing2_lot_ma_break_processes_morning_then_keeps_range_signal_for_next_pass(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)

        morning_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            client_order_id="BUY-T2-MORNING-MA-BREAK-005930",
            qty=3,
            price=10_200,
            executed_at="2026-04-17T09:01:00+09:00",
        )
        range_lot_id = _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            client_order_id="BUY-T2-RANGE-MA-BREAK-DEFER-005930",
            qty=2,
            price=10_000,
            executed_at="2026-04-17T10:00:30+09:00",
        )
        _store_complete_3m_closes(
            conn,
            symbol="005930",
            closes=[10_500, 10_400, 10_300, 10_200, 10_000],
        )

        scan_service = Timing2LotExitScanService(
            broker=_FakeBroker(
                {"005930": _make_price_snapshot(symbol="005930", price=10_600)}
            ),
            conn=conn,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 30, 0),
        )

        scan_result = scan_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2LotExitSettings(),
            write_signals=True,
        )

        assert scan_result.lot_count == 2
        assert scan_result.matched_count == 2
        assert scan_result.ma_break_count == 2
        assert scan_result.recorded_count == 2

        morning_signal = next(
            row for row in scan_result.recorded_signals if row.payload["lot_id"] == morning_lot_id
        )
        range_signal = next(
            row for row in scan_result.recorded_signals if row.payload["lot_id"] == range_lot_id
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot(
            symbol="005930",
            price=10_600,
        )
        order_service = _PersistingSellOrderService(conn, order_repo)

        execution_service = SellSignalExecutionService(
            broker=broker,
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            order_service=order_service,
            entry_lot_repo=entry_lot_repo,
            risk_guard_service=TradingRiskGuardService(
                order_repo=order_repo,
                trading_control_repo=TradingControlRepository(conn),
                daily_stats_repo=DailyStatsRepository(conn),
                now_fn=lambda: _kst_datetime(10, 30, 5),
            ),
            now_fn=lambda: _kst_datetime(10, 30, 5),
        )

        first_execution = execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=SellSignalExecutionSettings(),
            execute_orders=True,
        )

        assert first_execution.pending_signal_count == 2
        assert first_execution.candidate_count == 2
        assert first_execution.submitted_count == 1
        assert first_execution.blocked_count == 1
        assert first_execution.acted_count == 1
        assert first_execution.audit_record_count == 1

        first_submitted = next(
            item
            for item in first_execution.candidates
            if item.outcome == SellSignalExecutionOutcome.SUBMITTED
        )
        first_superseded = next(
            item
            for item in first_execution.candidates
            if item.outcome == SellSignalExecutionOutcome.BLOCKED
        )

        assert first_submitted.lot_id == morning_lot_id
        assert first_submitted.order_qty == 3
        assert first_superseded.lot_id == range_lot_id
        assert first_superseded.reason_code == "SUPERSEDED_BY_HIGHER_PRIORITY"
        assert signal_repo.get(morning_signal.id).acted is True
        assert signal_repo.get(range_signal.id).acted is False

        first_client_order_id = first_submitted.client_order_id
        assert first_client_order_id is not None
        import_service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
        )

        first_import = import_service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id=first_client_order_id,
                    kis_exec_no="SELL-MORNING-MA-BREAK-DEFER-1",
                    qty=3,
                    price=10_600,
                    executed_at="2026-04-17T10:31:00+09:00",
                )
            ],
            execute_import=True,
        )
        assert first_import.imported_count == 1

        morning_lot_after_first = entry_lot_repo.get(morning_lot_id)
        range_lot_after_first = entry_lot_repo.get(range_lot_id)
        assert morning_lot_after_first is not None
        assert morning_lot_after_first.remaining_qty == 0
        assert morning_lot_after_first.status == "CLOSED"
        assert range_lot_after_first is not None
        assert range_lot_after_first.remaining_qty == 2
        assert range_lot_after_first.status == "OPEN"

        second_execution = execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=SellSignalExecutionSettings(),
            execute_orders=True,
        )

        assert second_execution.pending_signal_count == 1
        assert second_execution.candidate_count == 1
        assert second_execution.submitted_count == 1
        assert second_execution.blocked_count == 0
        assert second_execution.acted_count == 1
        assert second_execution.audit_record_count == 1
        assert second_execution.candidates[0].lot_id == range_lot_id
        assert second_execution.candidates[0].order_qty == 2
        assert signal_repo.get(range_signal.id).acted is True

        second_client_order_id = second_execution.candidates[0].client_order_id
        assert second_client_order_id is not None
        second_import = import_service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id=second_client_order_id,
                    kis_exec_no="SELL-RANGE-MA-BREAK-DEFER-2",
                    qty=2,
                    price=10_600,
                    executed_at="2026-04-17T10:32:00+09:00",
                )
            ],
            execute_import=True,
        )
        assert second_import.imported_count == 1

        final_morning_lot = entry_lot_repo.get(morning_lot_id)
        final_range_lot = entry_lot_repo.get(range_lot_id)
        assert final_morning_lot is not None
        assert final_morning_lot.remaining_qty == 0
        assert final_morning_lot.realized_sell_qty == 3
        assert final_morning_lot.status == "CLOSED"
        assert final_range_lot is not None
        assert final_range_lot.remaining_qty == 0
        assert final_range_lot.realized_sell_qty == 2
        assert final_range_lot.status == "CLOSED"

        live_position = position_repo.get("005930")
        assert live_position is not None
        assert live_position.qty == 0
        assert len(order_service.calls) == 2
    finally:
        conn.close()
