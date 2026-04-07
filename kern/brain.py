"""
Kern-Jarvis V2 — LLM Abstraction Layer
"""
import logging
import threading
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kern.db import get_config
from kern.exceptions import LLMError, ConfigError

log = logging.getLogger(__name__)

KERN_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "kern.md"

# ── Client Cache ─────────────────────────────────────────────────────────────

_client_cache: dict[str, tuple[str, object]] = {}
_client_lock = threading.Lock()


def get_kern_prompt() -> str:
    if not KERN_PROMPT_PATH.exists():
        log.error("Kern-Prompt nicht gefunden: %s", KERN_PROMPT_PATH)
        return "Du bist Jarvis, ein hilfreicher KI-Assistent."
    return KERN_PROMPT_PATH.read_text()


_BERLIN_TZ = ZoneInfo("Europe/Berlin")
_WEEKDAYS_DE = [
    "Montag", "Dienstag", "Mittwoch", "Donnerstag",
    "Freitag", "Samstag", "Sonntag",
]


def _now_berlin_context() -> str:
    """Current Berlin date/time as a one-line dynamic prompt fragment.

    Injected on every turn so the model doesn't have to ask for the date
    before answering 'what's on today' style questions.
    """
    now = datetime.now(_BERLIN_TZ)
    weekday = _WEEKDAYS_DE[now.weekday()]
    return (
        f"Aktuelle Zeit: {weekday}, {now.strftime('%d.%m.%Y %H:%M')} "
        f"(Europe/Berlin)"
    )


def build_system_prompt(memory_context: str = "", tools_manifest: str = "") -> str:
    kern = get_kern_prompt()
    dynamic_parts = [_now_berlin_context()]
    if memory_context:
        dynamic_parts.append(memory_context)
    if tools_manifest:
        dynamic_parts.append(tools_manifest)
    return kern + "\n\n---\n\n" + "\n\n".join(dynamic_parts)


def get_llm_client() -> tuple[str, object]:
    """Get or create a cached LLM client for the configured provider."""
    provider = get_config("llm_provider", "anthropic")
    api_key = get_config("llm_api_key", "")

    if not api_key:
        raise ConfigError(
            "Kein API-Key konfiguriert. Setze ihn mit: /config set llm_api_key <key>"
        )

    cache_key = f"{provider}:{api_key}"
    with _client_lock:
        if cache_key in _client_cache:
            return _client_cache[cache_key]

    if provider == "anthropic":
        import anthropic
        result = ("anthropic", anthropic.Anthropic(api_key=api_key))
    elif provider == "openai":
        import openai
        result = ("openai", openai.OpenAI(api_key=api_key))
    elif provider == "openrouter":
        import openai
        result = ("openrouter", openai.OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1"
        ))
    else:
        raise ConfigError(f"Unbekannter Provider: {provider}")

    with _client_lock:
        _client_cache[cache_key] = result

    return result


def invalidate_client_cache() -> None:
    """Clear cached LLM clients (e.g. after config change)."""
    with _client_lock:
        _client_cache.clear()


def get_model() -> str:
    provider = get_config("llm_provider", "anthropic")
    saved = get_config("llm_model")
    if saved:
        return saved
    defaults = {
        "anthropic": "claude-sonnet-4-6",
        "openai": "gpt-4o",
        "openrouter": "anthropic/claude-sonnet-4-6",
    }
    return defaults.get(provider, "claude-opus-4-6")


def _extract_anthropic_text(response: object) -> str:
    if not response.content:
        raise LLMError("Leere Antwort vom LLM (kein Content)")
    return response.content[0].text


def _extract_openai_text(response: object) -> str:
    if not response.choices:
        raise LLMError("Leere Antwort vom LLM (keine Choices)")
    return response.choices[0].message.content or ""


def memory_chat(prompt: str, system: str = "", max_tokens: int = 256) -> str:
    """Cheap LLM call using the memory model for background operations."""
    provider, client = get_llm_client()
    model = get_config("memory_llm_model") or get_model()
    messages = [{"role": "user", "content": prompt}]

    try:
        if provider == "anthropic":
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system if system else "",
                messages=messages,
            )
            return _extract_anthropic_text(response)
        else:
            all_messages: list[dict] = []
            if system:
                all_messages.append({"role": "system", "content": system})
            all_messages.extend(messages)
            response = client.chat.completions.create(
                model=model,
                messages=all_messages,
                max_tokens=max_tokens,
                temperature=0,
            )
            return _extract_openai_text(response)
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"LLM-Aufruf fehlgeschlagen ({type(e).__name__}): {e}") from e


def chat(messages: list[dict], system: str = "") -> str:
    provider, client = get_llm_client()
    model = get_model()

    try:
        if provider == "anthropic":
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system,
                messages=messages
            )
            return _extract_anthropic_text(response)
        else:
            all_messages: list[dict] = []
            if system:
                all_messages.append({"role": "system", "content": system})
            all_messages.extend(messages)
            response = client.chat.completions.create(
                model=model,
                messages=all_messages,
                max_tokens=8192
            )
            return _extract_openai_text(response)
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"LLM-Aufruf fehlgeschlagen ({type(e).__name__}): {e}") from e


def chat_stream(messages: list[dict], system: str = "") -> Generator[str, None, None]:
    provider, client = get_llm_client()
    model = get_model()

    try:
        if provider == "anthropic":
            with client.messages.stream(
                model=model,
                max_tokens=8192,
                system=system,
                messages=messages
            ) as stream:
                for text in stream.text_stream:
                    yield text
        else:
            all_messages: list[dict] = []
            if system:
                all_messages.append({"role": "system", "content": system})
            all_messages.extend(messages)
            stream = client.chat.completions.create(
                model=model,
                messages=all_messages,
                max_tokens=8192,
                stream=True
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"LLM-Stream fehlgeschlagen ({type(e).__name__}): {e}") from e
