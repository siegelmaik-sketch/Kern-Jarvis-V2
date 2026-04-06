"""
Jarvis baut sich Tools selbst.
Erkennt REGISTER_TOOL, BUILD_TOOL, RUN_TOOL, MEMORY_SAVE/GET/SEARCH
Befehle in LLM-Antworten.
"""
import json
import logging
import re

from kern.tools import save_tool_script, register_tool, run_tool
from kern.exceptions import ToolSecurityError

log = logging.getLogger(__name__)

BUILD_TOOL_PROMPT = """Du sollst ein Python-Tool schreiben für folgende Aufgabe: {task}

Das Script MUSS folgende Struktur haben:
```python
# Tool: {tool_name}
# Beschreibung: {description}

def main(args: dict) -> dict:
    # args enthält die Eingabeparameter
    try:
        # Deine Implementierung hier
        result = ...
        return {{"success": True, "result": result, "error": None}}
    except Exception as e:
        return {{"success": False, "result": None, "error": str(e)}}
```

Schreibe NUR den Python-Code, keine Erklärungen darum herum.
Nutze nur Standard-Bibliotheken oder requests/httpx wenn nötig.
Das Tool soll robust und wiederverwendbar sein.
"""


def extract_code_block(text: str) -> str | None:
    match = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def build_tool(tool_name: str, description: str, task: str, auto_confirm: bool = False) -> dict:
    from kern.brain import chat

    log.info("Building tool: %s", tool_name)
    print(f"\n  Tool bauen: {tool_name}...")

    prompt = BUILD_TOOL_PROMPT.format(
        task=task,
        tool_name=tool_name,
        description=description
    )

    response = chat(
        messages=[{"role": "user", "content": prompt}],
        system="Du bist ein Python-Experte. Schreibe nur Code, keine Erklärungen."
    )

    code = extract_code_block(response)
    if not code:
        code = response.strip()

    # User-Bestätigung vor Ausführung
    print(f"\n  Generierter Code für '{tool_name}':")
    print("  " + "\n  ".join(code.split("\n")[:15]))
    if len(code.split("\n")) > 15:
        print(f"  ... ({len(code.split(chr(10)))} Zeilen gesamt)")

    if auto_confirm:
        confirm = "j"
    else:
        try:
            confirm = input("\n  Tool speichern und testen? [J/n] -> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return {"success": False, "error": "Abgebrochen"}

    if confirm in ("n", "nein", "no"):
        return {"success": False, "error": "Vom User abgelehnt"}

    try:
        script_path = save_tool_script(tool_name, code)
    except ToolSecurityError as e:
        log.error("Tool security violation: %s", e)
        return {"success": False, "error": str(e)}

    print(f"  Script gespeichert: {script_path}")

    test_result = run_tool_temp(tool_name, code, {})
    if test_result.get("success") is False and "error" in test_result:
        log.warning("Tool test failed [%s]: %s", tool_name, test_result["error"])
        print(f"  Warnung: {test_result['error']}")
    else:
        print("  Test OK")

    try:
        register_tool(tool_name, description, script_path)
    except ToolSecurityError as e:
        log.error("Tool registration failed: %s", e)
        return {"success": False, "error": str(e)}

    log.info("Tool built and registered: %s -> %s", tool_name, script_path)
    print("  Registriert im Manifest")

    return {"success": True, "tool_name": tool_name, "script_path": script_path}


def run_tool_temp(name: str, code: str, args: dict) -> dict:
    import importlib.util
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        spec = importlib.util.spec_from_file_location(name, tmp_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main(args)
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        os.unlink(tmp_path)


def parse_jarvis_commands(text: str) -> list[dict]:
    commands: list[dict] = []

    register_matches = re.finditer(
        r"REGISTER_TOOL\(name=['\"](.+?)['\"],\s*description=['\"](.+?)['\"],\s*script_path=['\"](.+?)['\"]\)",
        text
    )
    for m in register_matches:
        commands.append({
            "type": "register_tool",
            "name": m.group(1),
            "description": m.group(2),
            "script_path": m.group(3)
        })

    build_matches = re.finditer(
        r"BUILD_TOOL\(name=['\"](.+?)['\"],\s*description=['\"](.+?)['\"],\s*task=['\"](.+?)['\"]\)",
        text,
        re.DOTALL
    )
    for m in build_matches:
        commands.append({
            "type": "build_tool",
            "name": m.group(1),
            "description": m.group(2),
            "task": m.group(3)
        })

    memory_save_matches = re.finditer(
        r"MEMORY_SAVE\(type=['\"](.+?)['\"],\s*key=['\"](.+?)['\"],\s*value=['\"](.+?)['\"]\)",
        text,
        re.DOTALL
    )
    for m in memory_save_matches:
        commands.append({
            "type": "memory_save",
            "memory_type": m.group(1),
            "key": m.group(2),
            "value": m.group(3)
        })

    memory_get_matches = re.finditer(
        r"MEMORY_GET\(key=['\"](.+?)['\"]\)",
        text,
    )
    for m in memory_get_matches:
        commands.append({
            "type": "memory_get",
            "key": m.group(1),
        })

    memory_search_matches = re.finditer(
        r"MEMORY_SEARCH\(query=['\"](.+?)['\"]\)",
        text,
    )
    for m in memory_search_matches:
        commands.append({
            "type": "memory_search",
            "query": m.group(1),
        })

    run_matches = re.finditer(
        r"RUN_TOOL\(name=['\"](.+?)['\"](?:,\s*args=(\{.+?\}))?\)",
        text,
        re.DOTALL
    )
    for m in run_matches:
        args: dict = {}
        if m.group(2):
            try:
                args = json.loads(m.group(2))
            except json.JSONDecodeError:
                log.warning("RUN_TOOL args parse failed: %s", m.group(2)[:100])
        commands.append({
            "type": "run_tool",
            "name": m.group(1),
            "args": args
        })

    return commands


def execute_commands(commands: list[dict], auto_confirm: bool = False) -> list[dict]:
    from kern.memory import memory_save, search_fact_by_key, search_facts
    results: list[dict] = []

    for cmd in commands:
        try:
            if cmd["type"] == "build_tool":
                result = build_tool(cmd["name"], cmd["description"], cmd["task"], auto_confirm=auto_confirm)
                results.append(result)

            elif cmd["type"] == "register_tool":
                register_tool(cmd["name"], cmd["description"], cmd["script_path"])
                results.append({"success": True, "registered": cmd["name"]})

            elif cmd["type"] == "memory_save":
                memory_save(cmd["memory_type"], cmd["key"], cmd["value"])
                results.append({"success": True, "saved": cmd["key"]})

            elif cmd["type"] == "memory_get":
                facts = search_fact_by_key(cmd["key"])
                if facts:
                    results.append({"success": True, "result": "; ".join(f["fact"] for f in facts)})
                else:
                    results.append({"success": True, "result": f"Kein Eintrag für '{cmd['key']}'"})

            elif cmd["type"] == "memory_search":
                facts = search_facts(cmd["query"], limit=5)
                if facts:
                    results.append({"success": True, "result": "; ".join(f["fact"] for f in facts[:5])})
                else:
                    results.append({"success": True, "result": "Keine Ergebnisse"})

            elif cmd["type"] == "run_tool":
                result = run_tool(cmd["name"], cmd.get("args", {}))
                results.append(result)

        except ToolSecurityError as e:
            log.error("Security violation in command %s: %s", cmd["type"], e)
            results.append({"success": False, "error": f"Sicherheitsfehler: {e}"})
        except Exception as e:
            log.error("Command execution failed [%s]: %s", cmd["type"], e)
            results.append({"success": False, "error": str(e)})

    return results
