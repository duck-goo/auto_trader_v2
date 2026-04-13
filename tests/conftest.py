from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_TEMP_ROOT = PROJECT_ROOT / "data" / "test_runs"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
TEST_TEMPLATE_DB = TEST_TEMP_ROOT / "blank_template.db"

# Keep pytest away from the desktop app temp directory on this Windows setup.
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(TEST_TEMP_ROOT))
os.environ.setdefault("TMP", str(TEST_TEMP_ROOT))
os.environ.setdefault("TEMP", str(TEST_TEMP_ROOT))


@pytest.fixture
def test_db_path() -> Path:
    _ensure_blank_template_db()
    db_dir = TEST_TEMP_ROOT / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_{uuid4().hex}.db"
    shutil.copyfile(TEST_TEMPLATE_DB, db_path)
    return db_path


def _ensure_blank_template_db() -> None:
    if TEST_TEMPLATE_DB.exists():
        return

    source_db = _find_template_source_db()
    shutil.copyfile(source_db, TEST_TEMPLATE_DB)

    conn = sqlite3.connect(str(TEST_TEMPLATE_DB))
    try:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        for (table_name,) in rows:
            escaped_name = table_name.replace('"', '""')
            conn.execute(f'DROP TABLE IF EXISTS "{escaped_name}"')
        conn.commit()
    finally:
        conn.close()


def _find_template_source_db() -> Path:
    candidates = (
        PROJECT_ROOT / "data" / "debug.db",
        PROJECT_ROOT / "data" / "trading.db",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise RuntimeError(
        "No writable SQLite template source was found. "
        "Expected one of: data/debug.db, data/trading.db"
    )
