"""마이그레이션을 수동으로 한 문장씩 실행하면서 어디서 실패하는지 확인."""
from __future__ import annotations
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from storage.migrations.runner import _split_sql_statements  # noqa: E402

DB = PROJECT_ROOT / "data" / "debug.db"
if DB.exists():
    DB.unlink()
for ext in ("-wal", "-shm"):
    p = Path(str(DB) + ext)
    if p.exists():
        p.unlink()

sql_path = PROJECT_ROOT / "storage" / "migrations" / "001_init.sql"
sql = sql_path.read_text(encoding="utf-8")
stmts = _split_sql_statements(sql)

conn = sqlite3.connect(str(DB))
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")

print(f"{len(stmts)}개 문장 실행 시작\n")
for i, stmt in enumerate(stmts, 1):
    first = stmt.splitlines()[0][:70]
    try:
        conn.execute(stmt)
        print(f"[{i:02d}] OK   | {first}")
    except Exception as e:
        print(f"[{i:02d}] FAIL | {first}")
        print(f"       ↳ {type(e).__name__}: {e}")
        print(f"       ↳ 문장 전체 ({len(stmt)}자):")
        print("---")
        print(stmt)
        print("---")

print("\n=== 최종 테이블 ===")
for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(f"  - {r[0]}")

conn.close()