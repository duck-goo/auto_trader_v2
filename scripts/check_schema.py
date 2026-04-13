"""DB 스키마 빠른 확인용 스크립트."""
from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (scripts/ 하위에서 실행되기 때문)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings  # noqa: E402


def main() -> None:
    db_path = load_settings().db_path
    print(f"DB: {db_path}\n")

    # 운영 코드와 동일한 커넥션 팩토리 사용 (PRAGMA까지 정확히 반영)
    from storage.db import get_connection
    conn = get_connection(db_path)
    try:
        print("=== 테이블 목록 ===")
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for r in rows:
            print(f"  - {r[0]}")

        print("\n=== schema_version 적용 이력 ===")
        rows = conn.execute(
            "SELECT version, filename, applied_at "
            "FROM schema_version ORDER BY version"
        ).fetchall()
        for r in rows:
            print(f"  v{r[0]:03d} | {r[1]} | {r[2]}")

        print("\n=== PRAGMA 상태 ===")
        jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        print(f"  journal_mode  = {jm}")
        print(f"  foreign_keys  = {fk}")
        print(f"  busy_timeout  = {bt} ms")
    finally:
        conn.close()


if __name__ == "__main__":
    main()