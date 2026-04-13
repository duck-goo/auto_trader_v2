"""
storage.db + migrations 검증.
실제 KIS API 호출 없음. 임시 DB 파일에서 모든 검증 수행.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_migration_creates_all_tables(tmp_db: Path):
    count = run_migrations(tmp_db)
    assert count == 1

    conn = get_connection(tmp_db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r["name"] for r in rows}
    finally:
        conn.close()

    expected = {"orders", "executions", "positions", "signals",
                "daily_stats", "schema_version"}
    assert expected.issubset(names), f"누락된 테이블: {expected - names}"


def test_migration_idempotent(tmp_db: Path):
    """두 번 실행해도 안전해야 한다."""
    first = run_migrations(tmp_db)
    second = run_migrations(tmp_db)
    assert first == 1
    assert second == 0


def test_wal_mode_enabled(tmp_db: Path):
    run_migrations(tmp_db)
    conn = get_connection(tmp_db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_foreign_keys_enabled(tmp_db: Path):
    run_migrations(tmp_db)
    conn = get_connection(tmp_db)
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
    finally:
        conn.close()


def test_client_order_id_unique_constraint(tmp_db: Path):
    """멱등키 중복 INSERT는 반드시 실패해야 한다 (중복 주문 방지의 핵심)."""
    run_migrations(tmp_db)
    conn = get_connection(tmp_db)
    try:
        with transaction(conn):
            conn.execute(
                """INSERT INTO orders
                   (client_order_id, symbol, side, qty, price, order_type,
                    status, requested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("COID_001", "005930", "buy", 10, 70000, "LIMIT",
                 "PENDING", "2026-04-13T10:00:00+09:00"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            with transaction(conn):
                conn.execute(
                    """INSERT INTO orders
                       (client_order_id, symbol, side, qty, price, order_type,
                        status, requested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("COID_001", "005930", "buy", 10, 70000, "LIMIT",
                     "PENDING", "2026-04-13T10:00:01+09:00"),
                )
    finally:
        conn.close()


def test_execution_unique_per_order(tmp_db: Path):
    """동일 (order_id, kis_exec_no) 중복 INSERT는 실패해야 한다."""
    run_migrations(tmp_db)
    conn = get_connection(tmp_db)
    try:
        with transaction(conn):
            cur = conn.execute(
                """INSERT INTO orders
                   (client_order_id, symbol, side, qty, price, order_type,
                    status, requested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("COID_002", "005930", "buy", 10, 70000, "LIMIT",
                 "SUBMITTED", "2026-04-13T10:00:00+09:00"),
            )
            order_id = cur.lastrowid

            conn.execute(
                """INSERT INTO executions
                   (order_id, kis_exec_no, symbol, side, qty, price, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (order_id, "EXEC_X", "005930", "buy", 5, 70000,
                 "2026-04-13T10:00:05+09:00"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            with transaction(conn):
                conn.execute(
                    """INSERT INTO executions
                       (order_id, kis_exec_no, symbol, side, qty, price, executed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (order_id, "EXEC_X", "005930", "buy", 5, 70000,
                     "2026-04-13T10:00:06+09:00"),
                )
    finally:
        conn.close()


def test_transaction_rollback_on_exception(tmp_db: Path):
    """트랜잭션 도중 예외 → 변경사항이 남지 않아야 한다."""
    run_migrations(tmp_db)
    conn = get_connection(tmp_db)
    try:
        with pytest.raises(RuntimeError):
            with transaction(conn):
                conn.execute(
                    """INSERT INTO orders
                       (client_order_id, symbol, side, qty, price, order_type,
                        status, requested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("COID_ROLLBACK", "005930", "buy", 10, 70000, "LIMIT",
                     "PENDING", "2026-04-13T10:00:00+09:00"),
                )
                raise RuntimeError("의도된 실패")

        cnt = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE client_order_id=?",
            ("COID_ROLLBACK",),
        ).fetchone()[0]
        assert cnt == 0
    finally:
        conn.close()