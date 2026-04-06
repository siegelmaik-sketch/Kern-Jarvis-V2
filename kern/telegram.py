"""
Kern-Jarvis V2 — Telegram Bot Interface
Long-polling, single-user, with voice message transcription (Whisper)
and voice replies (OpenAI TTS).
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
_FILE_BASE = "https://api.telegram.org/file/bot{token}/{path}"
_process_lock = threading.Lock()
_bot_thread: threading.Thread | None = None

# Max chars sent to TTS — long responses get truncated for voice
_TTS_MAX_CHARS = 1000


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


def _send_voice(token: str, chat_id: int, audio: bytes) -> None:
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendVoice",
            data={"chat_id": str(chat_id)},
            files={"voice": ("reply.ogg", audio, "audio/ogg")},
            timeout=30,
        )
    except Exception as e:
        log.error("Telegram sendVoice failed: %s", e)


def _is_authorized(chat_id: int) -> bool:
    stored = get_config("telegram_chat_id")
    if not stored:
        set_config("telegram_chat_id", str(chat_id))
        log.info("Telegram: first contact authorized, chat_id=%s", chat_id)
        return True
    return str(chat_id) == stored


def _transcribe_voice(token: str, file_id: str) -> str | None:
    """Download a Telegram voice/audio file and transcribe it via OpenAI Whisper."""
    whisper_key = get_config("whisper_api_key")
    if not whisper_key:
        log.warning("Whisper API key not configured")
        return None

    try:
        file_info = httpx.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        file_info.raise_for_status()
        file_path = file_info.json()["result"]["file_path"]

        audio_resp = httpx.get(
            _FILE_BASE.format(token=token, path=file_path),
            timeout=30,
        )
        audio_resp.raise_for_status()

        transcription = httpx.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {whisper_key}"},
            files={"file": ("voice.ogg", audio_resp.content, "audio/ogg")},
            data={"model": "whisper-1"},
            timeout=30,
        )
        transcription.raise_for_status()
        return transcription.json().get("text", "").strip()

    except Exception as e:
        log.error("Voice transcription failed: %s", e)
        return None


def _synthesize_speech(text: str) -> bytes | None:
    """Convert text to speech via OpenAI TTS. Returns OGG/opus bytes."""
    api_key = get_config("whisper_api_key")
    if not api_key:
        log.warning("TTS: whisper_api_key not configured")
        return None

    voice = get_config("tts_voice", "nova")
    tts_text = text[:_TTS_MAX_CHARS]

    try:
        r = httpx.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "tts-1",
                "input": tts_text,
                "voice": voice,
                "response_format": "opus",
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.error("TTS synthesis failed: %s", e)
        return None


def _should_reply_with_voice(triggered_by_voice: bool) -> bool:
    mode = get_config("telegram_voice_replies", "auto")
    if mode == "always":
        return True
    if mode == "auto":
        return triggered_by_voice
    return False


def _process_message(token: str, chat_id: int, text: str, reply_with_voice: bool = False) -> None:
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

        if _should_reply_with_voice(reply_with_voice):
            audio = _synthesize_speech(response)
            if audio:
                _send_voice(token, chat_id, audio)
                # Send text too if response was truncated for TTS
                if len(response) > _TTS_MAX_CHARS:
                    _send(token, chat_id, response)
            else:
                # TTS failed — fall back to text
                _send(token, chat_id, response)
        else:
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


def _handle_update(token: str, update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat_id = msg.get("chat", {}).get("id")
    if not chat_id:
        return

    if not _is_authorized(chat_id):
        log.warning("Telegram: unauthorized chat_id=%s", chat_id)
        _send(token, chat_id, "Nicht autorisiert.")
        return

    # Text message
    text = msg.get("text", "").strip()
    if text:
        threading.Thread(
            target=_process_message,
            args=(token, chat_id, text, False),
            daemon=True,
        ).start()
        return

    # Voice or audio message
    voice = msg.get("voice") or msg.get("audio")
    if voice:
        file_id = voice.get("file_id")
        if not file_id:
            return

        if not get_config("whisper_api_key"):
            _send(token, chat_id, "Sprachnachrichten sind noch nicht eingerichtet.")
            return

        _send(token, chat_id, "🎙 Transkribiere...")
        transcript = _transcribe_voice(token, file_id)
        if transcript:
            threading.Thread(
                target=_process_message,
                args=(token, chat_id, f"[Sprachnachricht]: {transcript}", True),
                daemon=True,
            ).start()
        else:
            _send(token, chat_id, "Sprachnachricht konnte nicht transkribiert werden.")


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
                threading.Thread(
                    target=_handle_update,
                    args=(token, update),
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
