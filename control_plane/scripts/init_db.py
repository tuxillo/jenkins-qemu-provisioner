import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from control_plane.db import engine


def apply_migrations() -> None:
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
        applied = {row[0] for row in rows}

    raw = engine.raw_connection()
    try:
        for migration in sorted(migrations_dir.glob("*.sql")):
            version = migration.stem
            if version in applied:
                continue
            try:
                raw.executescript(migration.read_text(encoding="utf-8"))
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise
            raw.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).replace(tzinfo=None).isoformat()),
            )
        raw.commit()
    finally:
        raw.close()


if __name__ == "__main__":
    apply_migrations()
    print("migrations applied")
