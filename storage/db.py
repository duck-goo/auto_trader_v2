"""
SQLite 커넥션 팩토리 + 트랜잭션 컨텍스트.

설계:
    - WAL 모드: 동시 읽기 허용, 쓰기 충돌 완화
    - busy_timeout: 락 충돌 시 자동 대기 (ms)
    - foreign_keys ON: FK 제약 강제
    - row_factory=Row: 컬럼명 접근 가능
    - 트랜잭션은 컨텍스트 매니저로만 (예외 시 자동 ROLLBACK)

사용 예:
    with get_connection() as conn:
        with transaction(conn):
            conn.execute("INSERT ...")
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from logger import get_logger

_log = get_logger("system")


def get_connection(
    db_path: str | Path,
    *,
    busy_timeout_ms: int = 5000,
) -> sqlite3.Connection:
    """
    SQLite 커넥션 생성 + PRAGMA 설정.

    호출자가 close() 책임. with 구문 권장.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        timeout=busy_timeout_ms / 1000,
        isolation_level=None,  # autocommit; 트랜잭션은 명시적으로 BEGIN
        check_same_thread=False,  # Phase 3에서 멀티스레드 가능성 대비
    )
    conn.row_factory = sqlite3.Row

    # PRAGMA 설정
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL과 조합 시 안전+빠름

    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    명시적 트랜잭션 컨텍스트.

    BEGIN IMMEDIATE: 쓰기 락을 즉시 획득 → deadlock 회피.
    예외 발생 시 ROLLBACK, 정상 종료 시 COMMIT.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error as rollback_err:
            _log.error(f"ROLLBACK 실패: {rollback_err}")
        raise