"""
Jarvis Onboarding — einmaliger Setup beim ersten Start
"""
from kern.db import set_config, init_db
from kern.memory import memory_save


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
        ("anthropic/claude-opus-4-6", "Claude Opus via OpenRouter"),
        ("openai/gpt-4o", "GPT-4o via OpenRouter"),
        ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B — Open Source"),
    ],
}


def clear():
    print("\033[2J\033[H", end="")


def header():
    print("=" * 60)
    print("  JARVIS — Ersteinrichtung")
    print("=" * 60)
    print()


def run_onboarding():
    init_db()
    clear()
    header()

    print("Willkommen. Ich bin Jarvis — dein persistenter KI-Assistent.")
    print("Ich richte mich jetzt einmalig ein.\n")

    # Name
    name = input("Wie heißt du? → ").strip()
    if not name:
        name = "Nutzer"
    memory_save("user", "name", name)
    set_config("user_name", name)

    # Sprache
    print(f"\nHallo {name}. Welche Sprache bevorzugst du?")
    print("  1. Deutsch")
    print("  2. Englisch")
    lang_choice = input("→ ").strip()
    language = "de" if lang_choice != "2" else "en"
    set_config("language", language)
    memory_save("user", "sprache", "Deutsch" if language == "de" else "English")

    # Nutzung
    print(f"\nWofür möchtest du mich hauptsächlich nutzen?")
    print("(Freitext — z.B. 'Entwicklung, Automatisierung, Recherche')")
    usage = input("→ ").strip()
    if usage:
        memory_save("user", "hauptnutzung", usage)

    # LLM Provider
    print(f"\nWelchen KI-Provider möchtest du nutzen?")
    for k, (_, label) in PROVIDERS.items():
        print(f"  {k}. {label}")
    provider_choice = input("→ ").strip()
    provider_key, provider_label = PROVIDERS.get(provider_choice, ("anthropic", "Anthropic (Claude)"))
    set_config("llm_provider", provider_key)

    # Model
    print(f"\nWelches Modell?")
    models = MODELS.get(provider_key, [])
    for i, (model_id, model_label) in enumerate(models, 1):
        print(f"  {i}. {model_label}")
    model_choice = input("→ ").strip()
    try:
        model_id = models[int(model_choice) - 1][0]
    except Exception:
        model_id = models[0][0]
    set_config("llm_model", model_id)

    # API Key
    print(f"\nAPI-Key für {provider_label}:")
    api_key = input("→ ").strip()
    if api_key:
        set_config("llm_api_key", api_key)

    # Abschluss
    set_config("onboarding_done", "true")

    print()
    print("=" * 60)
    print(f"  Alles eingerichtet, {name}.")
    print(f"  Provider: {provider_label}")
    print(f"  Modell:   {model_id}")
    print("=" * 60)
    print()
    print("Ich starte jetzt. Du kannst mich alles fragen.")
    print("Wenn ich ein Tool brauche das ich noch nicht habe,")
    print("baue ich es mir selbst.\n")
    input("Weiter mit Enter...")
