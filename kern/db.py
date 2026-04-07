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
        _run_migrations(conn)
        conn.commit()


def _run_migrations(conn) -> None:
    """Idempotent column additions for DBs created before a schema bump.

    SQLite can't `ADD COLUMN IF NOT EXISTS`, so we check `PRAGMA table_info`
    first. Each migration must be safe to re-run.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tools)").fetchall()}
    if "args_schema" not in cols:
        conn.execute("ALTER TABLE tools ADD COLUMN args_schema TEXT")


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


# ── MCP Server Registry ───────────────────────────────────────────────────────

def add_mcp_server(name: str, url: str, headers: dict | None = None) -> None:
    import json
    with connection() as conn:
        conn.execute(
            "INSERT INTO mcp_servers (name, url, headers) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET url=excluded.url, headers=excluded.headers, enabled=1",
            (name, url, json.dumps(headers or {}))
        )
        conn.commit()


def remove_mcp_server(name: str) -> bool:
    with connection() as conn:
        cur = conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        conn.commit()
        return cur.rowcount > 0


def list_mcp_servers() -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM mcp_servers ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def get_mcp_server(name: str) -> dict | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM mcp_servers WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None
