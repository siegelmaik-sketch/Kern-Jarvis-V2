"""
Kern-Jarvis V2 Memory — Persistent, Untruncated
═════════════════════════════════════════════════
Tiers (wie V1):
1. messages   → every message ever, untruncated, append-only
2. archives   → semantic search via embeddings
3. facts      → persistent key facts with quality gate

Context loading: read newest messages until budget is full.
Nothing is ever truncated on write — only on load into the LLM context.

Embeddings via OpenRouter, stored as BLOB in SQLite.
Cosine similarity via numpy (no pgvector needed).
"""
import json
import logging
import re
import threading
from datetime import datetime

import httpx
import numpy as np

from kern.db import get_connection, get_config

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

CONTEXT_MAX_MESSAGES = 20
EMBEDDING_DIMS = 1024

# ── Embedding Client ─────────────────────────────────────────────────────────

_embed_client: httpx.Client | None = None
_embed_lock = threading.Lock()


def _get_embed_client() -> httpx.Client:
    global _embed_client
    with _embed_lock:
        if _embed_client is not None and not _embed_client.is_closed:
            return _embed_client
        api_key = get_config("embedding_api_key") or get_config("llm_api_key", "")
        _embed_client = httpx.Client(
            timeout=30,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        return _embed_client


def _get_embedding(text: str) -> np.ndarray | None:
    if not text.strip():
        return None
    try:
        client = _get_embed_client()
        model = get_config("embedding_model", "qwen/qwen3-embedding-8b")
        r = client.post(
            "https://openrouter.ai/api/v1/embeddings",
            json={
                "model": model,
                "input": text[:8000],
                "dimensions": EMBEDDING_DIMS,
            },
        )
        r.raise_for_status()
        data = r.json()
        vec = data["data"][0]["embedding"]
        return np.array(vec, dtype=np.float32)
    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError,
            KeyError, IndexError) as e:
        log.warning("Embedding failed: %s", e)
        return None


def _embedding_to_blob(vec: np.ndarray) -> bytes:
    return vec.tobytes()


def _blob_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0 or not np.isfinite(norm):
        return 0.0
    result = float(dot / norm)
    if not np.isfinite(result):
        return 0.0
    return result


# ── Messages: Append + Load ─────────────────────────────────────────────────

def append_message(msg: dict) -> None:
    """Append a single message to persistent storage. No truncation."""
    conn = get_connection()
    try:
        role = msg.get("role", "user")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")
        conn.execute(
            "INSERT INTO messages (role, content, tool_calls, tool_call_id) VALUES (?, ?, ?, ?)",
            (role, content, json.dumps(tool_calls) if tool_calls else None, tool_call_id)
        )
        conn.commit()
    finally:
        conn.close()


def load_context(max_messages: int = CONTEXT_MAX_MESSAGES) -> list[dict]:
    """Load last N messages for current conversation context.
    Returns messages in chronological order (oldest first).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content, tool_calls, tool_call_id FROM messages "
            "ORDER BY id DESC LIMIT ?",
            (max_messages,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    msgs = []
    for r in rows:
        msg = {"role": r["role"]}
        if r["content"] is not None:
            msg["content"] = r["content"]
        if r["tool_calls"]:
            msg["tool_calls"] = json.loads(r["tool_calls"])
        if r["tool_call_id"]:
            msg["tool_call_id"] = r["tool_call_id"]
        msgs.append(msg)

    msgs.reverse()
    return msgs


def get_message_count() -> int:
    conn = get_connection()
    try:
        count = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    finally:
        conn.close()
    return count


def clear_messages() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM messages")
        conn.commit()
    finally:
        conn.close()


# ── Fact Quality Gate ────────────────────────────────────────────────────────

def _gate_fact(fact: str, category: str) -> tuple[bool, int]:
    """LLM-based quality gate for agent-sourced facts.
    Returns (should_save, importance_score).
    """
    try:
        from kern.brain import memory_chat
        text = memory_chat(
            prompt=(
                'Rate this fact for long-term storage. Answer ONLY with JSON: '
                '{"save": true/false, "importance": 1-10}\n\n'
                'Rules:\n'
                '- SAVE: personal info, contacts, addresses, phone numbers, decisions, '
                'solutions to real bugs, architecture decisions, user preferences, '
                'important dates, real errors with root causes\n'
                '- DISCARD: agent status updates, general knowledge, Wikipedia-level info, '
                'task progress, implementation details\n\n'
                f'Fact: "{fact}"\nCategory: {category}'
            ),
            max_tokens=50,
        )
        parsed = _parse_llm_json(text)
        if parsed and "save" in parsed:
            return bool(parsed["save"]), int(parsed.get("importance", 5))
    except Exception as e:
        log.warning("Fact gate failed, allowing fact: %s", e)
    return True, 5


def _parse_llm_json(raw: str) -> dict | None:
    """Extract JSON from LLM response, handling ```json blocks."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[^}]+\}", raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


# ── Long-term Facts ──────────────────────────────────────────────────────────

def save_fact(
    fact: str,
    category: str = "general",
    source: str = "user",
    importance: int | None = None,
) -> bool:
    """Save a fact with quality gate for agent sources."""
    # Agent facts go through quality gate
    if source in ("agent", "implicit"):
        should_save, gate_importance = _gate_fact(fact, category)
        if not should_save:
            log.info("Fact gate rejected: %s", fact[:80])
            return False
        if importance is None:
            importance = gate_importance
    elif importance is None:
        importance = 7  # user/jarvis facts get high default importance

    # Generate embedding
    embedding = _get_embedding(fact)
    embedding_blob = _embedding_to_blob(embedding) if embedding is not None else None

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO facts (category, fact, source, importance, embedding) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(fact) DO NOTHING",
            (category, fact, source, importance, embedding_blob)
        )
        conn.commit()
        saved = cur.rowcount > 0
        if saved:
            log.info("Fact saved [%s/%s/imp=%d]: %s", source, category, importance, fact[:80])
        return saved
    except Exception as e:
        log.error("save_fact failed: %s", e)
        return False
    finally:
        conn.close()


def memory_save(memory_type: str, key: str, value: str) -> bool:
    """Wrapper for MEMORY_SAVE commands from LLM responses."""
    category_map = {
        "user": "preference",
        "feedback": "feedback",
        "project": "project",
        "reference": "reference",
    }
    category = category_map.get(memory_type, "general")
    fact_text = f"[{key}] {value}"
    return save_fact(fact=fact_text, category=category, source="jarvis", importance=7)


def get_facts(limit: int = 25) -> list[dict]:
    """Get facts ordered by importance then recency."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, category, fact, importance, source, created_at, last_accessed "
            "FROM facts ORDER BY importance DESC, created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_fact(fact_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        conn.commit()
    finally:
        conn.close()
    return cur.rowcount > 0


def search_fact_by_key(key: str) -> list[dict]:
    """Search facts by key prefix (for MEMORY_GET)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, category, fact, importance, source, created_at, last_accessed "
            "FROM facts WHERE fact LIKE ?",
            (f"[{key}]%",)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── Smart Fact Retrieval (3-Tier wie V1) ─────────────────────────────────────

def get_relevant_facts(query: str | None = None, limit: int = 15) -> list[dict]:
    """Smart fact retrieval: always-load + semantic + recency tiers."""
    conn = get_connection()
    try:
        collected: list[dict] = []
        seen_ids: set[int] = set()

        # Tier 1: Always-load (preference + system, high importance)
        always_rows = conn.execute(
            "SELECT id, category, fact, importance, source, created_at, last_accessed "
            "FROM facts WHERE category IN ('preference', 'system') "
            "ORDER BY importance DESC, created_at DESC LIMIT 5"
        ).fetchall()
        for r in always_rows:
            if r["id"] not in seen_ids:
                collected.append(dict(r))
                seen_ids.add(r["id"])

        # Tier 2: Semantic search (embeddings + cosine similarity)
        if query and len(collected) < limit:
            query_embedding = _get_embedding(query)
            if query_embedding is not None:
                all_with_emb = conn.execute(
                    "SELECT id, category, fact, importance, source, created_at, last_accessed, embedding "
                    "FROM facts WHERE embedding IS NOT NULL"
                ).fetchall()

                scored = []
                for r in all_with_emb:
                    if r["id"] in seen_ids:
                        continue
                    fact_embedding = _blob_to_embedding(r["embedding"])
                    sim = _cosine_similarity(query_embedding, fact_embedding)
                    if sim >= 0.2:
                        d = dict(r)
                        del d["embedding"]
                        d["similarity"] = round(sim, 3)
                        scored.append(d)

                scored.sort(key=lambda x: x["similarity"], reverse=True)
                for d in scored[:limit - len(collected)]:
                    collected.append(d)
                    seen_ids.add(d["id"])

        # Tier 3: Recent high-importance facts
        if len(collected) < limit:
            rec_rows = conn.execute(
                "SELECT id, category, fact, importance, source, created_at, last_accessed "
                "FROM facts WHERE importance >= 7 "
                "ORDER BY created_at DESC LIMIT ?",
                (limit - len(collected) + 5,)
            ).fetchall()
            for r in rec_rows:
                if r["id"] not in seen_ids:
                    collected.append(dict(r))
                    seen_ids.add(r["id"])
                    if len(collected) >= limit:
                        break

        # Update access tracking
        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            conn.execute(
                f"UPDATE facts SET last_accessed = CURRENT_TIMESTAMP, "
                f"access_count = access_count + 1 WHERE id IN ({placeholders})",
                list(seen_ids)
            )
            conn.commit()
    finally:
        conn.close()

    return collected[:limit]


def search_facts(query: str, limit: int = 20) -> list[dict]:
    """Semantic search over facts using embeddings."""
    query_embedding = _get_embedding(query)
    if query_embedding is None:
        return get_facts(limit)

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, category, fact, importance, source, created_at, last_accessed, embedding "
            "FROM facts WHERE embedding IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    scored = []
    for r in rows:
        fact_embedding = _blob_to_embedding(r["embedding"])
        sim = _cosine_similarity(query_embedding, fact_embedding)
        d = dict(r)
        del d["embedding"]
        d["similarity"] = round(sim, 3)
        scored.append(d)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:limit]


# ── Conversation Topic Tracker ───────────────────────────────────────────────

_conversation_topic: str = ""
_topic_keywords: list[str] = []
_topic_message_count: int = 0
_TOPIC_UPDATE_INTERVAL = 5
_topic_lock = threading.Lock()


def update_conversation_topic(recent_messages: list[dict]) -> None:
    """Update current conversation topic from recent messages."""
    global _conversation_topic, _topic_keywords, _topic_message_count

    with _topic_lock:
        _topic_message_count += 1
        if _topic_message_count % _TOPIC_UPDATE_INTERVAL != 0:
            return

    user_msgs = [
        m.get("content", "")[:200]
        for m in recent_messages[-10:]
        if m.get("role") == "user" and m.get("content")
    ][-5:]

    if not user_msgs:
        return

    text = "\n".join(user_msgs)
    try:
        from kern.brain import memory_chat
        result = memory_chat(
            prompt=(
                "What is the current topic of this conversation? "
                "Answer in max 10 words German. "
                "Also list 3-5 keywords.\n"
                "Format: TOPIC: ...\nKEYWORDS: kw1, kw2, kw3\n\n"
                f"Recent messages:\n{text}"
            ),
            max_tokens=60,
        )
        with _topic_lock:
            for line in result.split("\n"):
                line = line.strip()
                if line.upper().startswith("TOPIC:"):
                    _conversation_topic = line[6:].strip()
                elif line.upper().startswith("KEYWORDS:"):
                    _topic_keywords = [k.strip() for k in line[9:].split(",") if k.strip()]
        log.debug("Topic: %s | Keywords: %s", _conversation_topic, _topic_keywords)
    except Exception as e:
        log.debug("Topic update failed: %s", e)


def get_conversation_topic() -> str:
    with _topic_lock:
        return _conversation_topic


# ── Conversation Archive ────────────────────────────────────────────────────

def archive_conversation(
    topic: str,
    summary: str,
    keywords: list[str],
    messages: list[dict] | None = None,
) -> int | None:
    """Archive a conversation block with embedding for semantic search."""
    embed_text = f"{topic}\n{summary}"
    embedding = _get_embedding(embed_text)
    embedding_blob = _embedding_to_blob(embedding) if embedding is not None else None
    archived_msgs = json.dumps(messages, ensure_ascii=False) if messages else None

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO archives (topic, summary, keywords, messages, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            (topic, summary, json.dumps(keywords), archived_msgs, embedding_blob)
        )
        conn.commit()
        archive_id = cur.lastrowid
        log.info("Archived: %s (%d keywords, embedding=%s)",
                 topic, len(keywords), embedding is not None)
        return archive_id
    except Exception as e:
        log.error("archive_conversation failed: %s", e)
        return None
    finally:
        conn.close()


def search_archives(query: str, limit: int = 5) -> list[dict]:
    """Semantic search over archived conversations."""
    query_embedding = _get_embedding(query)
    if query_embedding is None:
        return []

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, topic, summary, keywords, created_at, embedding "
            "FROM archives WHERE embedding IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    scored = []
    for r in rows:
        arch_embedding = _blob_to_embedding(r["embedding"])
        sim = _cosine_similarity(query_embedding, arch_embedding)
        d = {
            "id": r["id"],
            "topic": r["topic"],
            "summary": r["summary"],
            "keywords": json.loads(r["keywords"]) if r["keywords"] else [],
            "created_at": r["created_at"],
            "similarity": round(sim, 3),
        }
        scored.append(d)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:limit]


# ── Build Memory Context for System Prompt ───────────────────────────────────

def build_memory_context(query: str | None = None) -> str:
    """Build complete memory context for the system prompt.
    Uses smart retrieval with semantic search if query provided.
    """
    facts = get_relevant_facts(query=query) if query else get_facts(limit=15)

    if not facts:
        return ""

    lines = ["## Memory — Bekannte Fakten\n"]
    for f in facts:
        imp = f.get("importance", 5)
        marker = "★" if imp >= 8 else "●" if imp >= 5 else "○"
        sim_info = f" (relevanz: {f['similarity']})" if "similarity" in f else ""
        lines.append(f"{marker} [{f['category']}] {f['fact']}{sim_info}")

    # Add relevant archives
    if query:
        archives = search_archives(query, limit=3)
        if archives:
            lines.append("\n## Relevante frühere Gespräche\n")
            for a in archives:
                lines.append(f"- **{a['topic']}**: {a['summary']} (relevanz: {a['similarity']})")

    return "\n".join(lines)
