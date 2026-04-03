"""
Kern-Jarvis Implicit Memory Extraction
═══════════════════════════════════════
Extrahiert nach einem Chat-Turn automatisch handlungsrelevante
Informationen: Zusagen, Entscheidungen, TODOs, Follow-up-Bedarf.

Konservativ und rauscharm: Nur bei hoher Confidence, nur operative
Informationen. Keine allgemeinen Fakten oder Smalltalk.

Portiert von Kern-Jarvis V1 — synchron für SQLite.
"""
import json
import logging
import re
from datetime import datetime

import httpx

from kern.db import get_config
from kern.memory import save_fact, get_memory_llm_model

log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

_MIN_CONVERSATION_LENGTH = 150
_last_extraction: datetime | None = None
_COOLDOWN_SECONDS = 120  # höchstens alle 2 Minuten

# ── Extraction Prompt ────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """Analysiere diesen Chat-Ausschnitt zwischen dem User und Jarvis (Assistent).
Extrahiere NUR handlungsrelevante operative Informationen. Sei SEHR konservativ — lieber nichts
extrahieren als etwas Falsches oder Irrelevantes.

Extrahiere NUR wenn klar und eindeutig:
- Konkrete Zusagen ("Ich mach das bis Freitag", "Ich ruf morgen an")
- Entscheidungen ("Wir nehmen Anbieter X", "Das machen wir nicht")
- Explizite TODOs die noch nicht erledigt sind
- Follow-up-Bedarf ("Muss noch bei X nachfragen")
- Neue wichtige operative Fakten (Kontaktdaten, Deadlines, Preise)
- Persönliche Infos über den User (Name, Rolle, Vorlieben)

NICHT extrahieren:
- Allgemeines Wissen, Smalltalk, Meinungen
- Dinge die Jarvis bereits mit Tools erledigt hat
- Vage Absichten ohne konkreten Handlungsbedarf
- Wiederholungen von bereits bekanntem

Antworte AUSSCHLIESSLICH als JSON-Array. Jedes Element:
{"type": "zusage|entscheidung|todo|followup|fakt|user_info", "content": "Was genau", "confidence": 0.0-1.0, "importance": 1-10}

Wenn nichts Relevantes gefunden: leeres Array [].
Maximal 3 Eintraege pro Turn. Nur Items mit confidence >= 0.7."""


def extract_from_conversation(user_message: str, assistant_reply: str) -> list[dict]:
    """Extract actionable information from a chat turn.
    Returns list of extracted items, or empty list.
    """
    global _last_extraction

    combined = f"{user_message}\n{assistant_reply}"
    if len(combined) < _MIN_CONVERSATION_LENGTH:
        return []

    now = datetime.now()
    if _last_extraction and (now - _last_extraction).total_seconds() < _COOLDOWN_SECONDS:
        return []

    if user_message.startswith("[SYSTEM]"):
        return []

    _last_extraction = now

    try:
        api_key = get_config("llm_api_key", "")
        client = httpx.Client(
            timeout=15,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        r = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": get_memory_llm_model(),
                "messages": [
                    {"role": "system", "content": _EXTRACTION_PROMPT},
                    {"role": "user", "content": f"Chat-Ausschnitt:\n\nUser: {user_message}\n\nJarvis: {assistant_reply}"},
                ],
                "max_tokens": 1024,
                "temperature": 0,
            },
        )
        r.raise_for_status()
        data = r.json()
        client.close()

        text = data["choices"][0]["message"]["content"].strip()

        # Parse JSON (handle markdown blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        items = json.loads(text)
        if not isinstance(items, list):
            return []

        # Filter by confidence
        valid_items = [
            item for item in items
            if isinstance(item, dict)
            and item.get("confidence", 0) >= 0.7
            and item.get("content")
        ]

        if valid_items:
            _store_items(valid_items)
            log.info("implicit_memory: %d Items extrahiert", len(valid_items))

        return valid_items

    except json.JSONDecodeError:
        log.debug("implicit_memory: JSON-Parsing fehlgeschlagen")
        return []
    except Exception as e:
        log.debug("implicit_memory: Extraktion fehlgeschlagen: %s", e)
        return []


def _store_items(items: list[dict]) -> None:
    """Store extracted items as facts in memory."""
    category_map = {
        "zusage": "commitment",
        "entscheidung": "decision",
        "todo": "todo",
        "followup": "followup",
        "fakt": "operational",
        "user_info": "preference",
    }

    type_labels = {
        "zusage": "Zusage",
        "entscheidung": "Entscheidung",
        "todo": "TODO",
        "followup": "Follow-up",
        "fakt": "Info",
        "user_info": "User-Info",
    }

    for item in items:
        content = item.get("content", "")
        item_type = item.get("type", "fakt")
        importance = min(item.get("importance", 5), 8)  # Cap at 8

        category = category_map.get(item_type, "operational")
        label = type_labels.get(item_type, "Info")
        tagged_content = f"[{label}] {content}"

        save_fact(
            category=category,
            fact=tagged_content,
            importance=importance,
            source="implicit",
        )
        log.debug("implicit_memory: Gespeichert [%s] %s (imp=%d)",
                  category, content[:60], importance)
