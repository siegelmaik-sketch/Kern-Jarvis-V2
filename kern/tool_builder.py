"""
Jarvis baut sich Tools selbst.
Erkennt REGISTER_TOOL und BUILD_TOOL Befehle in LLM-Antworten.
"""
import re
import json
from kern.tools import save_tool_script, register_tool, run_tool
from kern.brain import chat


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


def build_tool(tool_name: str, description: str, task: str) -> dict:
    print(f"\n🔧 Baue Tool: {tool_name}...")

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

    script_path = save_tool_script(tool_name, code)
    print(f"   Script gespeichert: {script_path}")

    test_result = run_tool_safe(tool_name, code, {})
    if test_result.get("success") is False and "error" in test_result:
        print(f"   ⚠️  Test-Warnung: {test_result['error']}")
    else:
        print(f"   ✓ Test OK")

    register_tool(tool_name, description, script_path)
    print(f"   ✓ Registriert im Manifest")

    return {"success": True, "tool_name": tool_name, "script_path": script_path}


def run_tool_safe(name: str, code: str, args: dict) -> dict:
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
    commands = []

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

    memory_matches = re.finditer(
        r"MEMORY_SAVE\(type=['\"](.+?)['\"],\s*key=['\"](.+?)['\"],\s*value=['\"](.+?)['\"]\)",
        text,
        re.DOTALL
    )
    for m in memory_matches:
        commands.append({
            "type": "memory_save",
            "memory_type": m.group(1),
            "key": m.group(2),
            "value": m.group(3)
        })

    run_matches = re.finditer(
        r"RUN_TOOL\(name=['\"](.+?)['\"](?:,\s*args=(\{.+?\}))?\)",
        text,
        re.DOTALL
    )
    for m in run_matches:
        args = {}
        if m.group(2):
            try:
                args = json.loads(m.group(2))
            except Exception:
                pass
        commands.append({
            "type": "run_tool",
            "name": m.group(1),
            "args": args
        })

    return commands


def execute_commands(commands: list[dict]) -> list[dict]:
    from kern.memory import memory_save
    results = []

    for cmd in commands:
        if cmd["type"] == "build_tool":
            result = build_tool(cmd["name"], cmd["description"], cmd["task"])
            results.append(result)

        elif cmd["type"] == "register_tool":
            register_tool(cmd["name"], cmd["description"], cmd["script_path"])
            results.append({"success": True, "registered": cmd["name"]})

        elif cmd["type"] == "memory_save":
            memory_save(cmd["memory_type"], cmd["key"], cmd["value"])
            results.append({"success": True, "saved": cmd["key"]})

        elif cmd["type"] == "run_tool":
            result = run_tool(cmd["name"], cmd.get("args", {}))
            results.append(result)

    return results
