"""
Kern-Loop — läuft ewig, kein Neustart nötig
"""
import sys
from kern.brain import build_system_prompt, chat_stream
from kern.memory import build_memory_context, memory_save, memory_get
from kern.tools import build_tools_manifest, run_tool, list_tools
from kern.tool_builder import parse_jarvis_commands, execute_commands
from kern.session import new_session, save_message, get_history
from kern.db import get_config


COMMANDS = {
    "/hilfe":   "Zeigt diese Hilfe",
    "/tools":   "Zeigt alle registrierten Tools",
    "/memory":  "Zeigt den aktuellen Memory-Inhalt",
    "/reset":   "Startet eine neue Session (kein Memory-Verlust)",
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
    from kern.memory import memory_all
    entries = memory_all()
    if not entries:
        print("\nMemory ist leer.\n")
        return
    print(f"\n{len(entries)} Memory-Einträge:")
    for e in entries:
        print(f"  [{e['type']}] {e['key']}: {e['value']}")
    print()


def get_prompt_prefix() -> str:
    name = get_config("user_name", "Du")
    return f"{name}"


def run_loop():
    session_id = new_session()
    name = get_config("user_name", "Du")

    print(f"\nJarvis bereit. Session: {session_id[:8]}...")
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
        elif user_input == "/reset":
            session_id = new_session()
            print(f"Neue Session: {session_id[:8]}...\n")
            continue

        # Nachricht speichern
        save_message(session_id, "user", user_input)

        # System-Prompt dynamisch aufbauen
        memory_ctx = build_memory_context()
        tools_manifest = build_tools_manifest()
        system = build_system_prompt(memory_ctx, tools_manifest)

        # Conversation History
        history = get_history(session_id)

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

        # Antwort speichern
        save_message(session_id, "assistant", full_response)

        # Jarvis-Befehle in der Antwort ausführen
        commands = parse_jarvis_commands(full_response)
        if commands:
            results = execute_commands(commands)
            for r in results:
                if r.get("success"):
                    if "tool_name" in r:
                        print(f"[Tool gebaut: {r['tool_name']}]")
                    elif "registered" in r:
                        print(f"[Tool registriert: {r['registered']}]")
                    elif "saved" in r:
                        pass  # Memory-Saves still im Hintergrund
                    elif "result" in r:
                        print(f"[Tool-Ergebnis: {r['result']}]")
                else:
                    if r.get("error"):
                        print(f"[Fehler: {r['error']}]")
