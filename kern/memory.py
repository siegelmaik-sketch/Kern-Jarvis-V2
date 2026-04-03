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
from datetime import datetime
from typing import Any

import httpx
import numpy as np

from kern.db import get_connection, get_config

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

CONTEXT_MAX_MESSAGES = 20
EMBEDDING_DIMS = 1024

# Defaults — werden von Config überschrieben wenn gesetzt
_DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
_DEFAULT_MEMORY_LLM_MODEL = "google/gemini-2.5-flash"


def get_embedding_model() -> str:
    return get_config("embedding_model", _DEFAULT_EMBEDDING_MODEL)


def get_memory_llm_model() -> str:
    return get_config("memory_llm_model", _DEFAULT_MEMORY_LLM_MODEL)

# ── Embedding Client ─────────────────────────────────────────────────────────

_embed_client: httpx.Client | None = None


def _get_embed_client() -> httpx.Client:
    global _embed_client
    if _embed_client is not None and not _embed_client.is_closed:
        return _embed_client
    api_key = get_config("llm_api_key", "")
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
        r = client.post(
            "https://openrouter.ai/api/v1/embeddings",
            json={
                "model": get_embedding_model(),
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
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(dot / norm)


# ── Messages: Append + Load ─────────────────────────────────────────────────

def append_message(msg: dict) -> None:
    """Append a single message to persistent storage. No truncation."""
    conn = get_connection()
    role = msg.get("role", "user")
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")
    tool_call_id = msg.get("tool_call_id")
    conn.execute(
        "INSERT INTO messages (role, content, tool_calls, tool_call_id) VALUES (?, ?, ?, ?)",
        (role, content, json.dumps(tool_calls) if tool_calls else None, tool_call_id)
    )
    conn.commit()
    conn.close()


def load_context(max_messages: int = CONTEXT_MAX_MESSAGES) -> list[dict]:
    """Load last N messages for current conversation context.
    Returns messages in chronological order (oldest first).
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content, tool_calls, tool_call_id FROM messages "
        "ORDER BY id DESC LIMIT ?",
        (max_messages + 10,)
    ).fetchall()
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

    msgs = msgs[:max_messages]
    msgs.reverse()
    return msgs


def get_message_count() -> int:
    conn = get_connection()
    count = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    conn.close()
    return count


def clear_messages() -> None:
    conn = get_connection()
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()


# ── Fact Quality Gate ────────────────────────────────────────────────────────

def _gate_fact(fact: str, category: str) -> tuple[bool, int]:
    """LLM-based quality gate for agent-sourced facts.
    Returns (should_save, importance_score).
    """
    try:
        client = _get_embed_client()
        r = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": get_memory_llm_model(),
                "messages": [{"role": "user", "content": (
                    'Rate this fact for long-term storage. Answer ONLY with JSON: '
                    '{"save": true/false, "importance": 1-10}\n\n'
                    'Rules:\n'
                    '- SAVE: personal info, contacts, addresses, phone numbers, decisions, '
                    'solutions to real bugs, architecture decisions, user preferences, '
                    'important dates, real errors with root causes\n'
                    '- DISCARD: agent status updates, general knowledge, Wikipedia-level info, '
                    'task progress, implementation details\n\n'
                    f'Fact: "{fact}"\nCategory: {category}'
                )}],
                "max_tokens": 50,
                "temperature": 0,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        parsed = _parse_llm_json(text)
        if parsed and "save" in parsed:
            return bool(parsed["save"]), int(parsed.get("importance", 5))
    except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError,
            KeyError, IndexError) as e:
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
        conn.execute(
            "INSERT INTO facts (category, fact, source, importance, embedding) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(fact) DO NOTHING",
            (category, fact, source, importance, embedding_blob)
        )
        conn.commit()
        saved = conn.total_changes > 0
        if saved:
            log.info("Fact saved [%s/%s/imp=%d]: %s", source, category, importance, fact[:80])
        return saved
    except Exception as e:
        log.error("save_fact failed: %s", e)
        return False
    finally:
        conn.close()


def get_facts(limit: int = 25) -> list[dict]:
    """Get facts ordered by importance then recency."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, category, fact, importance, source, created_at, last_accessed "
        "FROM facts ORDER BY importance DESC, created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_fact(fact_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ── Smart Fact Retrieval (3-Tier wie V1) ─────────────────────────────────────

_STOP_WORDS = {
    "der", "die", "das", "ein", "eine", "und", "oder", "aber", "ist", "sind",
    "war", "hat", "haben", "wird", "werden", "kann", "können", "soll", "mit",
    "von", "zu", "auf", "für", "in", "an", "bei", "nach", "über", "aus",
    "wie", "was", "wer", "wo", "wann", "nicht", "noch", "auch", "nur",
    "schon", "mal", "dann", "wenn", "als", "ich", "du", "er", "sie", "es",
    "wir", "ihr", "mein", "dein", "sein", "den", "dem", "des", "im", "am",
    "um", "ob", "so", "da", "hier", "dort", "jetzt", "heute", "gestern",
    "morgen", "bitte", "danke", "ja", "nein", "the", "is", "are", "was",
    "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "hast", "hab", "mach", "mir", "mich", "dir", "dich", "uns", "euch",
}


def _extract_search_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]{3,}", text.lower())
    keywords = [w for w in words if w not in _STOP_WORDS]
    seen = set()
    unique = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:10]


def get_relevant_facts(query: str | None = None, limit: int = 15) -> list[dict]:
    """Smart fact retrieval: always-load + semantic + recency tiers."""
    conn = get_connection()
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

    conn.close()
    return collected[:limit]


def search_facts(query: str, limit: int = 20) -> list[dict]:
    """Semantic search over facts using embeddings."""
    query_embedding = _get_embedding(query)
    if query_embedding is None:
        return get_facts(limit)

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, category, fact, importance, source, created_at, last_accessed, embedding "
        "FROM facts WHERE embedding IS NOT NULL"
    ).fetchall()
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


def update_conversation_topic(recent_messages: list[dict]) -> None:
    """Update current conversation topic from recent messages."""
    global _conversation_topic, _topic_keywords, _topic_message_count

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
        client = _get_embed_client()
        r = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": get_memory_llm_model(),
                "messages": [{"role": "user", "content": (
                    "What is the current topic of this conversation? "
                    "Answer in max 10 words German. "
                    "Also list 3-5 keywords.\n"
                    "Format: TOPIC: ...\nKEYWORDS: kw1, kw2, kw3\n\n"
                    f"Recent messages:\n{text}"
                )}],
                "max_tokens": 60,
                "temperature": 0,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        result = data["choices"][0]["message"]["content"].strip()
        for line in result.split("\n"):
            line = line.strip()
            if line.upper().startswith("TOPIC:"):
                _conversation_topic = line[6:].strip()
            elif line.upper().startswith("KEYWORDS:"):
                _topic_keywords = [k.strip() for k in line[9:].split(",") if k.strip()]
        log.debug("Topic: %s | Keywords: %s", _conversation_topic, _topic_keywords)
    except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError,
            KeyError, IndexError) as e:
        log.debug("Topic update failed: %s", e)


def get_conversation_topic() -> str:
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
    rows = conn.execute(
        "SELECT id, topic, summary, keywords, created_at, embedding "
        "FROM archives WHERE embedding IS NOT NULL"
    ).fetchall()
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
