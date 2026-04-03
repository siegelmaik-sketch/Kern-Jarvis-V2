"""
Kern-Jarvis V2 — SQLite Database Layer
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "jarvis.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@contextmanager
def connection():
    """Context manager for DB connections. Use: `with connection() as conn:`"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def get_connection() -> sqlite3.Connection:
    """Create a raw connection. Prefer `connection()` context manager."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()


def get_config(key: str, default: str | None = None) -> str | None:
    with connection() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value)
        )
        conn.commit()


def is_configured() -> bool:
    return get_config("onboarding_done") == "true"
