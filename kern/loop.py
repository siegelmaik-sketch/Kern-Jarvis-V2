"""
Kern-Loop — läuft ewig, kein Neustart nötig
"""
import logging
import queue
import sys
import threading

from kern.brain import build_system_prompt, chat_stream, invalidate_client_cache
from kern.memory import (
    build_memory_context, append_message, load_context, get_message_count,
    clear_messages, get_facts, update_conversation_topic,
)
from kern.tools import build_tools_manifest, list_tools
from kern.tool_builder import parse_jarvis_commands, execute_commands
from kern.implicit_memory import extract_from_conversation
from kern.db import get_config, set_config

log = logging.getLogger(__name__)

# Queue für Nachrichten aus Background-Threads (thread-safe)
_bg_messages: queue.Queue[str] = queue.Queue()


COMMANDS = {
    "/hilfe":   "Zeigt diese Hilfe",
    "/tools":   "Zeigt alle registrierten Tools",
    "/memory":  "Zeigt den aktuellen Memory-Inhalt",
    "/search":  "Semantische Suche in der Memory (z.B. /search Bitcoin)",
    "/config":  "Konfiguration anzeigen/ändern (z.B. /config set llm_model ...)",
    "/mcp":     "MCP-Server verwalten (add/remove/list/refresh)",
    "/reset":   "Löscht den Nachrichtenverlauf (Facts bleiben erhalten)",
    "/exit":    "Beendet Jarvis",
}


def print_help() -> None:
    print("\nBefehle:")
    for cmd, desc in COMMANDS.items():
        print(f"  {cmd:<12} {desc}")
    print()


def print_tools() -> None:
    tools = list_tools()
    if not tools:
        print("\nNoch keine Tools registriert.\n")
        return
    print(f"\n{len(tools)} Tool(s) registriert:")
    for t in tools:
        print(f"  • {t['name']}: {t['description']} (genutzt: {t['usage_count']}x)")
    print()


def print_memory() -> None:
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


_VALID_CONFIG_KEYS = {
    "llm_provider", "llm_model", "memory_llm_model", "embedding_model",
    "embedding_api_key", "llm_api_key", "user_name", "language",
}

_SECRET_KEYS = {"llm_api_key", "embedding_api_key"}


def _mask_value(key: str, value: str) -> str:
    """Mask sensitive config values for display."""
    if key in _SECRET_KEYS and len(value) > 4:
        return "***" + value[-4:]
    return value


def print_config(args: str) -> None:
    DISPLAY_KEYS = [
        "llm_provider", "llm_model", "memory_llm_model", "embedding_model",
        "user_name", "language",
    ]
    parts = args.split(maxsplit=2)

    if not parts or parts[0] not in ("set", "get"):
        print("\nAktuelle Konfiguration:")
        for key in DISPLAY_KEYS:
            val = get_config(key, "—")
            print(f"  {key}: {val}")
        api = get_config("llm_api_key", "")
        print(f"  llm_api_key: {'***' + api[-4:] if len(api) > 4 else '(nicht gesetzt)'}")
        embed_api = get_config("embedding_api_key", "")
        print(f"  embedding_api_key: {'***' + embed_api[-4:] if len(embed_api) > 4 else '(nutzt llm_api_key)'}")
        print("\nÄndern: /config set <key> <value>")
        print()
        return

    if parts[0] == "set" and len(parts) == 3:
        key, value = parts[1], parts[2]
        if key not in _VALID_CONFIG_KEYS:
            print(f"  Unbekannter Key: '{key}'")
            print(f"  Gültige Keys: {', '.join(sorted(_VALID_CONFIG_KEYS))}\n")
            return
        set_config(key, value)
        # Invalidate client cache on provider/key changes
        if key in ("llm_provider", "llm_api_key"):
            invalidate_client_cache()
        # Mask secret values in output
        display_value = _mask_value(key, value)
        print(f"  {key} -> {display_value}\n")
    elif parts[0] == "get" and len(parts) >= 2:
        key = parts[1]
        val = get_config(key, "—")
        display_val = _mask_value(key, val) if val != "—" else val
        print(f"  {key}: {display_val}\n")
    else:
        print("Verwendung: /config set <key> <value> | /config get <key> | /config\n")


def print_mcp(args: str) -> None:
    from kern.db import add_mcp_server, remove_mcp_server, list_mcp_servers
    from kern.mcp_client import fetch_tools, invalidate_cache, load_all_servers
    import json as _json

    parts = args.split(maxsplit=2)
    sub = parts[0] if parts else ""

    if sub == "add" and len(parts) >= 3:
        name, url = parts[1], parts[2]
        add_mcp_server(name, url)
        invalidate_cache(name)
        try:
            tools = fetch_tools(name, url)
            print(f"\n  MCP-Server '{name}' hinzugefügt — {len(tools)} Tool(s) geladen.")
            for t in tools:
                print(f"    • {t['name']}: {t['description']}")
        except Exception as e:
            print(f"\n  Server gespeichert, aber noch nicht erreichbar: {e}")
        print()

    elif sub == "remove" and len(parts) >= 2:
        from kern.db import remove_mcp_server
        name = parts[1]
        if remove_mcp_server(name):
            invalidate_cache(name)
            print(f"\n  MCP-Server '{name}' entfernt.\n")
        else:
            print(f"\n  Server '{name}' nicht gefunden.\n")

    elif sub == "list" or sub == "":
        servers = list_mcp_servers()
        if not servers:
            print("\n  Keine MCP-Server konfiguriert.")
            print("  Hinzufügen: /mcp add <name> <url>\n")
            return
        print(f"\n{len(servers)} MCP-Server:")
        for s in servers:
            status = "✓" if s["enabled"] else "✗"
            print(f"  [{status}] {s['name']} — {s['url']}")
        print()

    elif sub == "refresh":
        invalidate_cache()
        try:
            tools = load_all_servers()
            print(f"\n  MCP-Cache geleert — {len(tools)} Tool(s) neu geladen.\n")
        except Exception as e:
            print(f"\n  Fehler beim Refresh: {e}\n")

    else:
        print("\nVerwendung:")
        print("  /mcp list                    — alle Server anzeigen")
        print("  /mcp add <name> <url>        — Server hinzufügen")
        print("  /mcp remove <name>           — Server entfernen")
        print("  /mcp refresh                 — Tool-Cache neu laden\n")


def print_search(query: str) -> None:
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


def _flush_bg_messages() -> None:
    """Print queued messages from background threads (call from main thread only)."""
    while not _bg_messages.empty():
        try:
            msg = _bg_messages.get_nowait()
            print(msg)
        except queue.Empty:
            break


def _run_implicit_memory(user_input: str, response: str) -> None:
    """Run implicit memory extraction in background thread."""
    try:
        items = extract_from_conversation(user_input, response)
        if items:
            _bg_messages.put(f"  [Memory: {len(items)} Fakt(en) gelernt]")
    except Exception as e:
        log.warning("Implicit memory extraction failed: %s", e)


def run_loop() -> None:
    name = get_config("user_name", "Du")
    msg_count = get_message_count()

    print(f"\nJarvis bereit. {msg_count} Nachrichten im Langzeitgedächtnis.")
    print("Tippe /hilfe für Befehle oder stelle direkt eine Frage.\n")

    while True:
        _flush_bg_messages()

        try:
            user_input = input(f"{name} -> ").strip()
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
        elif user_input.startswith("/mcp"):
            print_mcp(user_input[4:].strip())
            continue
        elif user_input == "/reset":
            clear_messages()
            print("Nachrichtenverlauf gelöscht. Facts bleiben erhalten.\n")
            continue

        # Nachricht persistent speichern (append-only)
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
        print("\nJarvis -> ", end="", flush=True)
        chunks: list[str] = []
        stream_ok = False

        try:
            for chunk in chat_stream(history, system=system):
                print(chunk, end="", flush=True)
                chunks.append(chunk)
            stream_ok = True
        except Exception as e:
            log.error("Stream failed: %s", e)
            print(f"\n[Fehler: {e}]")

        print("\n")

        full_response = "".join(chunks)

        if not stream_ok or not full_response.strip():
            # Save error marker to prevent orphaned user message
            append_message({
                "role": "assistant",
                "content": "[Fehler: Antwort konnte nicht generiert werden]",
            })
            continue

        append_message({"role": "assistant", "content": full_response})

        # Jarvis-Befehle in der Antwort ausführen
        commands = parse_jarvis_commands(full_response)
        if commands:
            results = execute_commands(commands)
            result_parts: list[str] = []
            for r in results:
                if r.get("success"):
                    if "tool_name" in r:
                        print(f"  [Tool gebaut: {r['tool_name']}]")
                    elif "registered" in r:
                        print(f"  [Tool registriert: {r['registered']}]")
                    elif "result" in r:
                        print(f"  [Tool-Ergebnis: {r['result']}]")
                        result_parts.append(str(r["result"]))
                else:
                    if r.get("error"):
                        print(f"  [Fehler: {r['error']}]")
                        result_parts.append(f"Fehler: {r['error']}")

            # Persist tool results so LLM sees them next turn
            if result_parts:
                append_message({
                    "role": "assistant",
                    "content": "[Tool-Ergebnisse]\n" + "\n".join(result_parts),
                })

        # Implicit Memory: im Hintergrund Fakten extrahieren
        t = threading.Thread(
            target=_run_implicit_memory,
            args=(user_input, full_response),
            daemon=True
        )
        t.start()
