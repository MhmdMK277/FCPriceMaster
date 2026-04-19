"""
Numbered migration runner.
Usage: uv run python -m backend.src.db.migrate
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parents[4] / "data" / "fcpricemaster.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    # executescript required: DEFAULT (strftime(...)) is rejected by conn.execute()
    # even on SQLite 3.50 when called from Python's sqlite3 module.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            filename    TEXT NOT NULL UNIQUE,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        """
    )
    conn.commit()


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT filename FROM _migrations").fetchall()
    return {row[0] for row in rows}


def run_migrations(db_path: str | None = None) -> None:
    if db_path is not None:
        import sqlite3 as _sq
        from pathlib import Path as _Path
        _Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = _sq.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    else:
        conn = get_connection()
    ensure_migrations_table(conn)
    already_applied = applied_migrations(conn)

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        print("No migration files found.")
        return

    applied_count = 0
    for path in sql_files:
        name = path.name
        if name in already_applied:
            print(f"  skip  {name}")
            continue

        print(f"  apply {name}")
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (name,))
        conn.commit()
        applied_count += 1

    print(f"\nDone. {applied_count} migration(s) applied.")
    conn.close()


if __name__ == "__main__":
    run_migrations()
