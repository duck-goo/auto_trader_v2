"""
순차 마이그레이션 러너.

규칙:
    - 파일명: NNN_<설명>.sql (NNN은 0-padded 3자리)
    - schema_version 테이블에 적용 이력 기록
    - 이미 적용된 버전은 스킵 (멱등)
    - 각 파일은 단일 트랜잭션으로 적용 (중간 실패 시 롤백)
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from logger import get_logger
from storage.db import get_connection, transaction

_log = get_logger("system")

MIGRATIONS_DIR = Path(__file__).parent
_FILENAME_PATTERN = re.compile(r"^(\d{3})_.+\.sql$")

def _split_sql_statements(sql: str) -> list[str]:
    """
    SQL 스크립트를 개별 문장 리스트로 분리 (문자 단위 스캐너).

    처리 가능:
        - 라인 주석 (-- ...)
        - 블록 주석 (/* ... */)
        - 홑/겹 따옴표 문자열 리터럴
        - 문자열 안 세미콜론 무시
    미지원:
        - BEGIN ... END 블록 (트리거)  ← 현재 마이그레이션에서 미사용
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_single = False     # '...' 문자열
    in_double = False     # "..." 식별자
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # 블록 주석 종료
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        # 라인 주석 종료 (줄바꿈)
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
            i += 1
            continue

        # 문자열 안
        if in_single:
            buf.append(ch)
            if ch == "'":
                # '' 이스케이프 처리
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        # 주석 시작 감지
        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue

        # 문자열 시작
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue

        # 문장 종료
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        # 일반 문자
        buf.append(ch)
        i += 1

    # 남은 버퍼
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)

    return statements

def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            filename   TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )


def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {row["version"] for row in rows}


def _discover_migrations() -> list[tuple[int, Path]]:
    """파일명을 파싱해 (version, path) 정렬 리스트 반환."""
    found: list[tuple[int, Path]] = []
    for path in MIGRATIONS_DIR.iterdir():
        if not path.is_file():
            continue
        m = _FILENAME_PATTERN.match(path.name)
        if not m:
            continue
        version = int(m.group(1))
        found.append((version, path))

    found.sort(key=lambda x: x[0])

    # 중복 버전 방지
    versions = [v for v, _ in found]
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"중복된 마이그레이션 버전 발견: {versions}")

    return found


def run_migrations(db_path: str | Path) -> int:
    """
    모든 미적용 마이그레이션 실행.

    Returns:
        새로 적용된 마이그레이션 개수
    """
    applied_count = 0
    conn = get_connection(db_path)
    try:
        _ensure_schema_version_table(conn)
        already_applied = _get_applied_versions(conn)
        migrations = _discover_migrations()

        for version, path in migrations:
            if version in already_applied:
                _log.debug(f"마이그레이션 스킵 (이미 적용): {path.name}")
                continue

            sql = path.read_text(encoding="utf-8")
            _log.info(f"마이그레이션 적용 중: {path.name}")

            try:
                statements = _split_sql_statements(sql)
                if not statements:
                    raise RuntimeError(
                        f"마이그레이션 파일에 실행 가능한 SQL이 없습니다: {path.name}"
                    )
                with transaction(conn):
                    for stmt in statements:
                        conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO schema_version (version, filename) VALUES (?, ?)",
                        (version, path.name),
                    )
                _log.info(
                    f"마이그레이션 적용 완료: {path.name} "
                    f"({len(statements)}개 SQL 문장)"
                )
                applied_count += 1
            except Exception as e:
                _log.error(f"마이그레이션 실패: {path.name} | {e}")
                raise

        if applied_count == 0:
            _log.info("적용할 새 마이그레이션 없음")
        else:
            _log.info(f"총 {applied_count}개 마이그레이션 적용 완료")

        return applied_count
    finally:
        conn.close()


if __name__ == "__main__":
    # CLI 실행: python -m storage.migrations.runner [db_path]
    from config.loader import load_settings

    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        db_path = load_settings().db_path

    print(f"DB: {db_path}")
    count = run_migrations(db_path)
    print(f"적용: {count}건")