from kern.db import get_connection


VALID_TYPES = ("user", "feedback", "project", "reference")


def memory_save(type: str, key: str, value: str) -> bool:
    if type not in VALID_TYPES:
        return False
    conn = get_connection()
    conn.execute(
        "INSERT INTO memory (type, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
        (type, key, value)
    )
    conn.commit()
    conn.close()
    return True


def memory_get(key: str) -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT value FROM memory WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def memory_search(query: str) -> list[dict]:
    conn = get_connection()
    q = f"%{query}%"
    rows = conn.execute(
        "SELECT type, key, value FROM memory WHERE key LIKE ? OR value LIKE ? ORDER BY updated_at DESC LIMIT 20",
        (q, q)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def memory_all(type: str = None) -> list[dict]:
    conn = get_connection()
    if type:
        rows = conn.execute(
            "SELECT type, key, value, updated_at FROM memory WHERE type = ? ORDER BY updated_at DESC",
            (type,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT type, key, value, updated_at FROM memory ORDER BY updated_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def memory_delete(key: str) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM memory WHERE key = ?", (key,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def build_memory_context() -> str:
    entries = memory_all()
    if not entries:
        return ""
    lines = ["## Memory\n"]
    for t in VALID_TYPES:
        items = [e for e in entries if e["type"] == t]
        if items:
            lines.append(f"### {t.capitalize()}")
            for item in items:
                lines.append(f"- **{item['key']}**: {item['value']}")
    return "\n".join(lines)
