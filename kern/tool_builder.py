"""
Jarvis baut sich Tools selbst.
Erkennt REGISTER_TOOL, BUILD_TOOL, RUN_TOOL, MEMORY_SAVE/GET/SEARCH
Befehle in LLM-Antworten.

Tool-Builder Priorität:
1. Claude Code CLI (wenn installiert + authentifiziert) — beste Qualität
2. LLM via brain.chat() — Fallback
"""
import json
import logging
import os
import re
import shutil
import subprocess

from kern.tools import TOOLS_DIR, save_tool_script, register_tool, run_tool
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


CLAUDE_CODE_TOOL_PROMPT = """Write a Python tool for Kern-Jarvis V2.

Tool name: {tool_name}
Description: {description}
Task: {task}

REQUIREMENTS:
1. Save the tool to: {script_path}
2. The tool MUST have this exact structure:

```python
# Tool: {tool_name}
# Description: {description}

def main(args: dict) -> dict:
    try:
        # implementation here
        result = ...
        return {{"success": True, "result": result, "error": None}}
    except Exception as e:
        return {{"success": False, "result": None, "error": str(e)}}
```

3. Only use stdlib or already-installed packages: httpx, numpy, openai, anthropic
4. After writing, verify syntax: python3 -c "import py_compile; py_compile.compile('{script_path}')"
5. Do NOT register the tool in any database — just write and verify the file.
6. If the task requires internet access, use httpx (already available).
"""


def _find_claude_bin() -> str | None:
    """Find the claude CLI binary."""
    return shutil.which("claude")


def _claude_code_available() -> bool:
    """Check if Claude Code is installed and authenticated."""
    bin_path = _find_claude_bin()
    if not bin_path:
        return False
    # Check for OAuth credentials or API key
    has_oauth = os.path.isfile(os.path.expanduser("~/.claude/.credentials.json"))
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return has_oauth or has_key


def _build_tool_with_claude_code(tool_name: str, description: str, task: str) -> dict:
    """Use Claude Code CLI to write a tool. Returns success dict."""
    claude_bin = _find_claude_bin()
    if not claude_bin:
        return {"success": False, "error": "Claude Code not found"}

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    script_path = TOOLS_DIR / f"{tool_name}.py"

    prompt = CLAUDE_CODE_TOOL_PROMPT.format(
        tool_name=tool_name,
        description=description,
        task=task,
        script_path=script_path,
    )

    log.info("Building tool with Claude Code: %s", tool_name)
    print(f"\n  Claude Code baut Tool: {tool_name}...")

    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--dangerously-skip-permissions", "--output-format", "text"],
            cwd=str(TOOLS_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Claude Code timeout (180s)"}
    except Exception as e:
        return {"success": False, "error": f"Claude Code subprocess error: {e}"}

    if not script_path.exists():
        log.warning("Claude Code did not create file: %s\nstdout: %s\nstderr: %s",
                    script_path, result.stdout[:500], result.stderr[:500])
        return {"success": False, "error": "Claude Code hat keine Datei erstellt"}

    # Verify syntax
    verify = subprocess.run(
        ["python3", "-c", f"import py_compile; py_compile.compile('{script_path}')"],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        script_path.unlink(missing_ok=True)
        return {"success": False, "error": f"Syntax-Fehler: {verify.stderr.strip()}"}

    log.info("Claude Code built tool successfully: %s", script_path)
    return {"success": True, "script_path": str(script_path)}


def extract_code_block(text: str) -> str | None:
    match = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def build_tool(tool_name: str, description: str, task: str, auto_confirm: bool = False) -> dict:
    log.info("Building tool: %s", tool_name)

    # ── Strategy 1: Claude Code CLI ──────────────────────────────────────────
    if _claude_code_available():
        print(f"\n  Tool bauen via Claude Code: {tool_name}...")

        if not auto_confirm:
            try:
                confirm = input("\n  Claude Code beauftragen? [J/n] -> ").strip().lower()
                if confirm in ("n", "nein", "no"):
                    return {"success": False, "error": "Abgebrochen"}
            except (KeyboardInterrupt, EOFError):
                return {"success": False, "error": "Abgebrochen"}

        cc_result = _build_tool_with_claude_code(tool_name, description, task)

        if cc_result["success"]:
            script_path = cc_result["script_path"]
            print(f"  Script erstellt: {script_path}")
            try:
                register_tool(tool_name, description, script_path)
            except ToolSecurityError as e:
                log.error("Tool registration failed: %s", e)
                return {"success": False, "error": str(e)}
            log.info("Tool built (Claude Code) and registered: %s", tool_name)
            print("  Registriert im Manifest")
            return {"success": True, "tool_name": tool_name, "script_path": script_path}

        log.warning("Claude Code failed for %s: %s — falling back to LLM", tool_name, cc_result["error"])
        print(f"  Claude Code fehlgeschlagen ({cc_result['error']}) — Fallback auf LLM...")

    # ── Strategy 2: LLM via brain.chat() ─────────────────────────────────────
    from kern.brain import chat

    print(f"\n  Tool bauen via LLM: {tool_name}...")

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

    log.info("Tool built (LLM) and registered: %s -> %s", tool_name, script_path)
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
