import uuid
from kern.db import get_connection


def new_session() -> str:
    return str(uuid.uuid4())


def save_message(session_id: str, role: str, content: str, tool_name: str = None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO conversations (session_id, role, content, tool_name) VALUES (?, ?, ?, ?)",
        (session_id, role, content, tool_name)
    )
    conn.commit()
    conn.close()


def get_history(session_id: str, limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content FROM conversations "
        "WHERE session_id = ? AND role IN ('user', 'assistant') "
        "ORDER BY created_at DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
