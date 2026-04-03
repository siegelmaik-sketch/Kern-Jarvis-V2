"""
Kern-Loop — läuft ewig, kein Neustart nötig
"""
import sys
import threading
from kern.brain import build_system_prompt, chat_stream
from kern.memory import (
    build_memory_context, append_message, load_context, get_message_count,
    clear_messages, get_facts, get_relevant_facts, save_fact,
    update_conversation_topic,
)
from kern.tools import build_tools_manifest, run_tool, list_tools
from kern.tool_builder import parse_jarvis_commands, execute_commands
from kern.implicit_memory import extract_from_conversation
from kern.db import get_config, set_config


COMMANDS = {
    "/hilfe":   "Zeigt diese Hilfe",
    "/tools":   "Zeigt alle registrierten Tools",
    "/memory":  "Zeigt den aktuellen Memory-Inhalt",
    "/search":  "Semantische Suche in der Memory (z.B. /search Bitcoin)",
    "/config":  "Konfiguration anzeigen/ändern (z.B. /config set llm_model ...)",
    "/reset":   "Löscht den Nachrichtenverlauf (Facts bleiben erhalten)",
    "/exit":    "Beendet Jarvis",
}


def print_help():
    print("\nBefehle:")
    for cmd, desc in COMMANDS.items():
        print(f"  {cmd:<12} {desc}")
    print()


def print_tools():
    tools = list_tools()
    if not tools:
        print("\nNoch keine Tools registriert.\n")
        return
    print(f"\n{len(tools)} Tool(s) registriert:")
    for t in tools:
        print(f"  • {t['name']}: {t['description']} (genutzt: {t['usage_count']}x)")
    print()


def print_memory():
    facts = get_facts(limit=50)
    if not facts:
        print("\nMemory ist leer.\n")
        return
    print(f"\n{len(facts)} Fakt(en) in der Memory:")
    for f in facts:
        imp = f.get("importance", 5)
        marker = "★" if imp >= 8 else "●" if imp >= 5 else "○"
        print(f"  {marker} [{f['category']}] {f['fact']} (imp={imp}, src={f.get('source', '?')})")
    print(f"\n  Nachrichten gesamt: {get_message_count()}")
    print()


def print_config(args: str):
    CONFIG_KEYS = [
        "llm_provider", "llm_model", "memory_llm_model", "embedding_model",
        "user_name", "language",
    ]
    parts = args.split(maxsplit=2)

    if not parts or parts[0] not in ("set", "get"):
        print("\nAktuelle Konfiguration:")
        for key in CONFIG_KEYS:
            val = get_config(key, "—")
            print(f"  {key}: {val}")
        api = get_config("llm_api_key", "")
        print(f"  llm_api_key: {'***' + api[-4:] if len(api) > 4 else '(nicht gesetzt)'}")
        print(f"\nÄndern: /config set <key> <value>")
        print()
        return

    if parts[0] == "set" and len(parts) == 3:
        key, value = parts[1], parts[2]
        set_config(key, value)
        print(f"  {key} → {value}\n")
    elif parts[0] == "get" and len(parts) >= 2:
        val = get_config(parts[1], "—")
        print(f"  {parts[1]}: {val}\n")
    else:
        print("Verwendung: /config set <key> <value> | /config get <key> | /config\n")


def print_search(query: str):
    from kern.memory import search_facts
    if not query:
        print("Verwendung: /search <suchbegriff>")
        return
    results = search_facts(query, limit=10)
    if not results:
        print(f"\nKeine Ergebnisse für '{query}'.\n")
        return
    print(f"\n{len(results)} Ergebnis(se) für '{query}':")
    for f in results:
        sim = f.get("similarity", 0)
        print(f"  [{sim:.1%}] [{f['category']}] {f['fact']}")
    print()


def _run_implicit_memory(user_input: str, response: str):
    """Run implicit memory extraction in background thread."""
    try:
        items = extract_from_conversation(user_input, response)
        if items:
            labels = [f"[{i.get('type', '?')}] {i.get('content', '')[:40]}" for i in items]
            print(f"  [Memory: {len(items)} Fakt(en) gelernt]")
    except Exception:
        pass  # never crash the loop


def run_loop():
    name = get_config("user_name", "Du")
    msg_count = get_message_count()

    print(f"\nJarvis bereit. {msg_count} Nachrichten im Langzeitgedächtnis.")
    print("Tippe /hilfe für Befehle oder stelle direkt eine Frage.\n")

    while True:
        try:
            user_input = input(f"{name} → ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nJarvis beendet.")
            sys.exit(0)

        if not user_input:
            continue

        # Interne Befehle
        if user_input == "/exit":
            print("Jarvis beendet.")
            sys.exit(0)
        elif user_input == "/hilfe":
            print_help()
            continue
        elif user_input == "/tools":
            print_tools()
            continue
        elif user_input == "/memory":
            print_memory()
            continue
        elif user_input.startswith("/search"):
            print_search(user_input[7:].strip())
            continue
        elif user_input.startswith("/config"):
            print_config(user_input[7:].strip())
            continue
        elif user_input == "/reset":
            clear_messages()
            print("Nachrichtenverlauf gelöscht. Facts bleiben erhalten.\n")
            continue

        # Nachricht persistent speichern (append-only, nie gelöscht)
        append_message({"role": "user", "content": user_input})

        # System-Prompt dynamisch aufbauen — mit semantischer Suche
        memory_ctx = build_memory_context(query=user_input)
        tools_manifest = build_tools_manifest()
        system = build_system_prompt(memory_ctx, tools_manifest)

        # Letzte Nachrichten als Kontext laden
        history = load_context()

        # Topic-Tracker updaten
        update_conversation_topic(history)

        # LLM aufrufen (streaming)
        print(f"\nJarvis → ", end="", flush=True)
        full_response = ""

        try:
            for chunk in chat_stream(history, system=system):
                print(chunk, end="", flush=True)
                full_response += chunk
        except Exception as e:
            print(f"\n[Fehler: {e}]")
            continue

        print("\n")

        # Antwort persistent speichern
        append_message({"role": "assistant", "content": full_response})

        # Jarvis-Befehle in der Antwort ausführen
        commands = parse_jarvis_commands(full_response)
        if commands:
            results = execute_commands(commands)
            for r in results:
                if r.get("success"):
                    if "tool_name" in r:
                        print(f"  [Tool gebaut: {r['tool_name']}]")
                    elif "registered" in r:
                        print(f"  [Tool registriert: {r['registered']}]")
                    elif "result" in r:
                        print(f"  [Tool-Ergebnis: {r['result']}]")
                else:
                    if r.get("error"):
                        print(f"  [Fehler: {r['error']}]")

        # Implicit Memory: im Hintergrund Fakten extrahieren
        t = threading.Thread(
            target=_run_implicit_memory,
            args=(user_input, full_response),
            daemon=True
        )
        t.start()
