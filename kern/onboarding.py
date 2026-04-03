"""
Jarvis Onboarding — einmaliger Setup beim ersten Start
"""
import logging
import sys

import httpx
from kern.db import set_config, get_config
from kern.memory import save_fact

log = logging.getLogger(__name__)

PROVIDERS = {
    "1": ("anthropic", "Anthropic (Claude)"),
    "2": ("openai", "OpenAI (GPT)"),
    "3": ("openrouter", "OpenRouter (Multi-Model)"),
}

MODELS = {
    "anthropic": [
        ("claude-opus-4-6", "Claude Opus 4.6 — stärkste Intelligenz"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 — Geschwindigkeit + Qualität"),
    ],
    "openai": [
        ("gpt-4o", "GPT-4o — stärkstes OpenAI Modell"),
        ("gpt-4o-mini", "GPT-4o Mini — schnell + günstig"),
    ],
    "openrouter": [
        ("anthropic/claude-opus-4-6", "Claude Opus 4.6 via OpenRouter"),
        ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6 via OpenRouter"),
        ("google/gemini-2.5-pro-preview-03-25", "Gemini 2.5 Pro Preview"),
        ("openai/gpt-4o", "GPT-4o via OpenRouter"),
        ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B — Open Source"),
    ],
}

MEMORY_MODELS = {
    "openrouter": [
        ("google/gemini-2.5-flash", "Gemini 2.5 Flash — schnell + günstig (empfohlen)"),
        ("google/gemini-2.5-pro-preview-03-25", "Gemini 2.5 Pro — besser aber teurer"),
        ("anthropic/claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ("openai/gpt-4o-mini", "GPT-4o Mini"),
        ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B"),
    ],
    "anthropic": [
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5 — schnell + günstig (empfohlen)"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ],
    "openai": [
        ("gpt-4o-mini", "GPT-4o Mini — schnell + günstig (empfohlen)"),
        ("gpt-4o", "GPT-4o"),
    ],
}

EMBEDDING_MODELS = [
    ("qwen/qwen3-embedding-8b", "Qwen3 Embedding 8B — 1024 dims (empfohlen)"),
    ("openai/text-embedding-3-small", "OpenAI Embedding 3 Small — 1536 dims"),
    ("openai/text-embedding-3-large", "OpenAI Embedding 3 Large — 3072 dims"),
]


def clear() -> None:
    print("\033[2J\033[H", end="")


def header() -> None:
    print("=" * 60)
    print("  JARVIS — Ersteinrichtung")
    print("=" * 60)
    print()


def _choose(options: list[tuple[str, str]], prompt: str = "->") -> str:
    """Helper: zeigt nummerierte Liste, gibt gewählten Key zurück."""
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    choice = input(f"{prompt} ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return options[idx][0]
    except ValueError:
        pass
    print(f"  (Ungültige Wahl, nutze Standard: {options[0][1]})")
    return options[0][0]


def _validate_api_key(provider: str, api_key: str) -> bool:
    """Test ob der API-Key funktioniert mit einem minimalen Request."""
    try:
        if provider == "anthropic":
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=15,
            )
            if r.status_code in (401, 403):
                return False
            if r.status_code >= 500:
                log.warning("API validation got server error %d — assuming valid", r.status_code)
            return True

        elif provider == "openai":
            r = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if r.status_code in (401, 403):
                return False
            if r.status_code >= 500:
                log.warning("API validation got server error %d — assuming valid", r.status_code)
            return True

        elif provider == "openrouter":
            r = httpx.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            return r.status_code == 200

    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("API validation failed: %s", e)
        print("  (Verbindungstest fehlgeschlagen — Key wird trotzdem gespeichert)")
    return True


def run_onboarding() -> None:
    try:
        _do_onboarding()
    except (KeyboardInterrupt, EOFError):
        print("\n\nOnboarding abgebrochen.")
        sys.exit(1)


def _do_onboarding() -> None:
    clear()
    header()

    api_key_saved = False

    print("Willkommen. Ich bin Jarvis — dein persistenter KI-Assistent.")
    print("Ich richte mich jetzt einmalig ein.\n")

    # ── Name ─────────────────────────────────────────────────────────────
    name = input("Wie heißt du? -> ").strip()
    if not name:
        name = "Nutzer"
    save_fact(f"User heißt {name}", category="preference", source="user", importance=9)
    set_config("user_name", name)

    # ── Sprache ──────────────────────────────────────────────────────────
    print(f"\nHallo {name}. Welche Sprache bevorzugst du?")
    print("  1. Deutsch")
    print("  2. Englisch")
    lang_choice = input("-> ").strip()
    language = "de" if lang_choice != "2" else "en"
    set_config("language", language)
    lang_label = "Deutsch" if language == "de" else "English"
    save_fact(f"Bevorzugte Sprache: {lang_label}", category="preference", source="user", importance=8)

    # ── Nutzung ──────────────────────────────────────────────────────────
    print("\nWofür möchtest du mich hauptsächlich nutzen?")
    print("(Freitext — z.B. 'Entwicklung, Automatisierung, Recherche')")
    usage = input("-> ").strip()
    if usage:
        save_fact(f"Hauptnutzung: {usage}", category="preference", source="user", importance=7)

    # ── LLM Provider ─────────────────────────────────────────────────────
    print("\nWelchen KI-Provider möchtest du nutzen?")
    for k, (_, label) in PROVIDERS.items():
        print(f"  {k}. {label}")
    provider_choice = input("-> ").strip()
    provider_key, provider_label = PROVIDERS.get(provider_choice, ("anthropic", "Anthropic (Claude)"))
    set_config("llm_provider", provider_key)

    # ── API Key ──────────────────────────────────────────────────────────
    print(f"\nAPI-Key für {provider_label}:")
    api_key = input("-> ").strip()
    if api_key:
        print("  Prüfe Key...", end=" ", flush=True)
        if _validate_api_key(provider_key, api_key):
            print("OK")
            set_config("llm_api_key", api_key)
            api_key_saved = True
        else:
            print("UNGÜLTIG")
            print("  Der API-Key wurde nicht akzeptiert. Bitte prüfe ihn.")
            retry = input("  Trotzdem speichern? [j/N] -> ").strip().lower()
            if retry in ("j", "ja", "y", "yes"):
                set_config("llm_api_key", api_key)
                api_key_saved = True
            else:
                print("  Key nicht gespeichert. Setze ihn später mit: /config set llm_api_key <key>")

    # ── Haupt-Modell ─────────────────────────────────────────────────────
    models = MODELS.get(provider_key, [])
    print("\nWelches Haupt-Modell? (für Conversations + Tool-Building)")
    model_id = _choose(models)
    set_config("llm_model", model_id)

    # ── Memory-Modell (günstig) ──────────────────────────────────────────
    memory_models = MEMORY_MODELS.get(provider_key, [])
    print("\nWelches Memory-Modell? (für Quality Gate, Implicit Memory — sollte günstig sein)")
    memory_model_id = _choose(memory_models)
    set_config("memory_llm_model", memory_model_id)

    # ── Embedding-Modell ─────────────────────────────────────────────────
    print("\nWelches Embedding-Modell? (für semantische Suche in der Memory)")
    if provider_key == "openrouter":
        embedding_model_id = _choose(EMBEDDING_MODELS)
    else:
        print("  Hinweis: Embeddings laufen über OpenRouter.")
        print("  Falls du einen anderen Provider für Conversations nutzt,")
        print("  braucht Jarvis trotzdem einen OpenRouter-Key für Embeddings.")
        or_key = input("\n  OpenRouter API-Key (für Embeddings): -> ").strip()
        if or_key:
            set_config("embedding_api_key", or_key)
        embedding_model_id = _choose(EMBEDDING_MODELS)
    set_config("embedding_model", embedding_model_id)

    # ── Abschluss ────────────────────────────────────────────────────────
    if not api_key_saved:
        print("\n  WARNUNG: Kein API-Key gespeichert!")
        print("  Jarvis braucht einen Key zum Funktionieren.")
        print("  Setze ihn mit: /config set llm_api_key <key>")

    set_config("onboarding_done", "true")

    print()
    print("=" * 60)
    print(f"  Alles eingerichtet, {name}.")
    print(f"  Provider:        {provider_label}")
    print(f"  Haupt-Modell:    {model_id}")
    print(f"  Memory-Modell:   {memory_model_id}")
    print(f"  Embedding-Modell:{embedding_model_id}")
    print("=" * 60)
    print()
    print("Ich starte jetzt. Du kannst mich alles fragen.")
    print("Wenn ich ein Tool brauche das ich noch nicht habe,")
    print("baue ich es mir selbst.")
    print()
    print("Alle Modelle können jederzeit geändert werden über:")
    print("  /config set llm_model <modell-id>")
    print("  /config set memory_llm_model <modell-id>")
    print("  /config set embedding_model <modell-id>")
    print()
    input("Weiter mit Enter...")
