"""_split_sql_statements 동작 검증."""
from __future__ import annotations
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from storage.migrations.runner import _split_sql_statements  # noqa: E402

sql_path = PROJECT_ROOT / "storage" / "migrations" / "001_init.sql"
sql = sql_path.read_text(encoding="utf-8")
stmts = _split_sql_statements(sql)

print(f"총 {len(stmts)}개 문장 분리됨\n")
for i, s in enumerate(stmts, 1):
    lines = s.strip().splitlines()
    print(f"[{i:02d}] ({len(lines)}줄, {len(s)}자)")
    print(f"     시작: {lines[0][:80]}")
    if len(lines) > 1:
        print(f"     끝  : {lines[-1][:80]}")
    print()