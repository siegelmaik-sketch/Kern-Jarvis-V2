import importlib.util
import sys
import json
from pathlib import Path
from kern.db import get_connection

TOOLS_DIR = Path(__file__).parent.parent / "tools"


def register_tool(name: str, description: str, script_path: str) -> bool:
    conn = get_connection()
    conn.execute(
        "INSERT INTO tools (name, description, script_path) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET description=excluded.description, script_path=excluded.script_path",
        (name, description, script_path)
    )
    conn.commit()
    conn.close()
    return True


def get_tool(name: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM tools WHERE name = ?", (name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_tools() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tools ORDER BY usage_count DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_tool(name: str, args: dict = None) -> dict:
    tool = get_tool(name)
    if not tool:
        return {"success": False, "error": f"Tool '{name}' nicht gefunden"}

    script_path = Path(tool["script_path"])
    if not script_path.exists():
        return {"success": False, "error": f"Script nicht gefunden: {script_path}"}

    try:
        spec = importlib.util.spec_from_file_location(name, script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.main(args or {})

        conn = get_connection()
        conn.execute(
            "UPDATE tools SET usage_count = usage_count + 1, last_used_at = CURRENT_TIMESTAMP WHERE name = ?",
            (name,)
        )
        conn.commit()
        conn.close()

        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def save_tool_script(name: str, code: str) -> str:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOLS_DIR / f"{name}.py"
    path.write_text(code)
    return str(path)


def build_tools_manifest() -> str:
    tools = list_tools()
    if not tools:
        return "## Tools\nNoch keine Tools registriert.\n"
    lines = ["## Verfügbare Tools\n"]
    for t in tools:
        lines.append(f"- **{t['name']}**: {t['description']} (genutzt: {t['usage_count']}x)")
    return "\n".join(lines)
