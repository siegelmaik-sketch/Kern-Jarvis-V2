"""
Kern-Jarvis V2 — Tool Registry + Execution
"""
import importlib.util
import logging
import re
from pathlib import Path

from kern.db import connection
from kern.exceptions import ToolSecurityError

log = logging.getLogger(__name__)

TOOLS_DIR = Path(__file__).parent.parent / "tools"
_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_tool_name(name: str) -> None:
    if not _VALID_TOOL_NAME.match(name):
        raise ToolSecurityError(
            f"Ungültiger Tool-Name: {name!r}. Nur Buchstaben, Zahlen, _ und - erlaubt."
        )


def _validate_script_path(path: str) -> None:
    resolved = Path(path).resolve()
    tools_resolved = TOOLS_DIR.resolve()
    if not resolved.is_relative_to(tools_resolved):
        raise ToolSecurityError(
            f"Script-Pfad muss innerhalb von {TOOLS_DIR} liegen: {path}"
        )


def register_tool(name: str, description: str, script_path: str) -> bool:
    _validate_tool_name(name)
    _validate_script_path(script_path)

    with connection() as conn:
        conn.execute(
            "INSERT INTO tools (name, description, script_path) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET description=excluded.description, script_path=excluded.script_path",
            (name, description, script_path)
        )
        conn.commit()
    log.info("Tool registered: %s -> %s", name, script_path)
    return True


def get_tool(name: str) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM tools WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def list_tools() -> list[dict]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM tools ORDER BY usage_count DESC").fetchall()
        return [dict(r) for r in rows]


def run_tool(name: str, args: dict | None = None) -> dict:
    tool = get_tool(name)
    if not tool:
        return {"success": False, "error": f"Tool '{name}' nicht gefunden"}

    script_path = Path(tool["script_path"])
    if not script_path.exists():
        return {"success": False, "error": f"Script nicht gefunden: {script_path}"}

    # Validate path is within TOOLS_DIR
    try:
        _validate_script_path(str(script_path))
    except ToolSecurityError as e:
        log.error("Tool security violation: %s", e)
        return {"success": False, "error": str(e)}

    try:
        spec = importlib.util.spec_from_file_location(name, script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "main"):
            return {"success": False, "error": f"Tool '{name}' hat keine main()-Funktion"}

        result = module.main(args or {})

        with connection() as conn:
            conn.execute(
                "UPDATE tools SET usage_count = usage_count + 1, last_used_at = CURRENT_TIMESTAMP WHERE name = ?",
                (name,)
            )
            conn.commit()

        log.info("Tool executed: %s (args=%s)", name, args)
        return result
    except Exception as e:
        log.error("Tool execution failed [%s]: %s", name, e)
        return {"success": False, "error": str(e)}


def save_tool_script(name: str, code: str) -> str:
    _validate_tool_name(name)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOLS_DIR / f"{name}.py"
    path.write_text(code)
    log.info("Tool script saved: %s", path)
    return str(path)


def build_tools_manifest() -> str:
    tools = list_tools()
    if not tools:
        return ""
    lines = ["## Verfügbare Tools\n"]
    for t in tools:
        lines.append(f"- **{t['name']}**: {t['description']} (genutzt: {t['usage_count']}x)")
    return "\n".join(lines)
