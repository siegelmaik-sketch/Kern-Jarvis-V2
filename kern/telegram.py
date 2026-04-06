"""
Kern-Jarvis V2 — Telegram Bot Interface
Long-polling, single-user.
"""
import logging
import threading
import time

import httpx

from kern.brain import build_system_prompt, chat
from kern.db import get_config, set_config
from kern.implicit_memory import extract_from_conversation
from kern.memory import append_message, build_memory_context, load_context, update_conversation_topic
from kern.tool_builder import execute_commands, parse_jarvis_commands
from kern.tools import build_tools_manifest

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_process_lock = threading.Lock()
_bot_thread: threading.Thread | None = None


def _api(token: str, method: str, **params) -> dict:
    url = _API_BASE.format(token=token, method=method)
    r = httpx.post(url, json=params, timeout=35)
    r.raise_for_status()
    return r.json()


def _send(token: str, chat_id: int, text: str) -> None:
    for i in range(0, max(len(text), 1), 4096):
        try:
            _api(token, "sendMessage", chat_id=chat_id, text=text[i:i + 4096])
        except Exception as e:
            log.error("Telegram sendMessage failed: %s", e)


def _is_authorized(chat_id: int) -> bool:
    stored = get_config("telegram_chat_id")
    if not stored:
        set_config("telegram_chat_id", str(chat_id))
        log.info("Telegram: first contact authorized, chat_id=%s", chat_id)
        return True
    return str(chat_id) == stored


def _process_message(token: str, chat_id: int, text: str) -> None:
    with _process_lock:
        append_message({"role": "user", "content": text})

        memory_ctx = build_memory_context(query=text)
        tools_manifest = build_tools_manifest()
        system = build_system_prompt(memory_ctx, tools_manifest)
        history = load_context()
        update_conversation_topic(history)

        try:
            response = chat(history, system=system)
        except Exception as e:
            log.error("Telegram LLM call failed: %s", e)
            _send(token, chat_id, f"Fehler: {e}")
            return

        append_message({"role": "assistant", "content": response})
        _send(token, chat_id, response)

        commands = parse_jarvis_commands(response)
        if commands:
            results = execute_commands(commands, auto_confirm=True)
            result_parts: list[str] = []
            for r in results:
                if r.get("success"):
                    if "tool_name" in r:
                        _send(token, chat_id, f"[Tool gebaut: {r['tool_name']}]")
                    elif "result" in r:
                        result_parts.append(str(r["result"]))
                        _send(token, chat_id, str(r["result"]))
                elif r.get("error"):
                    _send(token, chat_id, f"[Fehler: {r['error']}]")

            if result_parts:
                append_message({
                    "role": "assistant",
                    "content": "[Tool-Ergebnisse]\n" + "\n".join(result_parts),
                })

        threading.Thread(
            target=_extract_memory,
            args=(text, response),
            daemon=True,
        ).start()


def _extract_memory(user_input: str, response: str) -> None:
    try:
        extract_from_conversation(user_input, response)
    except Exception as e:
        log.warning("Telegram implicit memory failed: %s", e)


def _poll_loop(token: str) -> None:
    log.info("Telegram bot started (long-polling)")
    offset = 0
    while True:
        try:
            data = _api(token, "getUpdates", offset=offset, timeout=30)
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                text = msg.get("text", "").strip()
                chat_id = msg.get("chat", {}).get("id")
                if not text or not chat_id:
                    continue
                if not _is_authorized(chat_id):
                    log.warning("Telegram: unauthorized chat_id=%s", chat_id)
                    _send(token, chat_id, "Nicht autorisiert.")
                    continue
                threading.Thread(
                    target=_process_message,
                    args=(token, chat_id, text),
                    daemon=True,
                ).start()
        except httpx.TimeoutException:
            pass  # Normal for long-polling
        except Exception as e:
            log.error("Telegram polling error: %s", e)
            time.sleep(5)


def start(token: str) -> None:
    """Start the Telegram bot in a background daemon thread (idempotent)."""
    global _bot_thread
    if _bot_thread is not None and _bot_thread.is_alive():
        log.debug("Telegram bot already running")
        return
    _bot_thread = threading.Thread(target=_poll_loop, args=(token,), daemon=True, name="telegram-bot")
    _bot_thread.start()
    log.info("Telegram bot thread started")
