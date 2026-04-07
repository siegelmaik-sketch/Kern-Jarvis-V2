"""
Kern-Jarvis V2 — Tool Registry + Execution

Local tools: stored in SQLite + tools/ dir, executed via importlib.
MCP tools:   proxied to MCP servers, prefixed with "mcp__<server>__<tool>".
"""
import importlib.util
import json
import logging
import re
from pathlib import Path

from kern.db import connection, list_mcp_servers
from kern.exceptions import ToolSecurityError

log = logging.getLogger(__name__)

TOOLS_DIR = Path(__file__).parent.parent / "tools"
_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
MCP_PREFIX = "mcp__"


# ── Builtin Tools ─────────────────────────────────────────────────────────────
# Foundational capabilities baked into kern/. The LLM calls these via the same
# RUN_TOOL(name=...) mechanism as user-built tools, but they dispatch to Python
# functions instead of dynamic scripts. They cannot be removed or overwritten.

def _builtin_web_search(args: dict) -> dict:
    from kern.web import web_search
    from kern.exceptions import WebSearchAPIError

    query = args.get("query", "").strip()
    max_results = int(args.get("max_results", 5))
    if not query:
        return {"success": False, "error": "Argument 'query' fehlt"}
    try:
        results = web_search(query, max_results=max_results)
        return {"success": True, "result": results}
    except WebSearchAPIError as e:
        return {"success": False, "error": str(e)}
    except ValueError as e:
        return {"success": False, "error": f"Ungültige Argumente: {e}"}


def _builtin_web_fetch(args: dict) -> dict:
    from kern.web import web_fetch
    from kern.exceptions import WebFetchError

    url = args.get("url", "").strip()
    max_chars = int(args.get("max_chars", 8000))
    if not url:
        return {"success": False, "error": "Argument 'url' fehlt"}
    try:
        result = web_fetch(url, max_chars=max_chars)
        return {"success": True, "result": result}
    except WebFetchError as e:
        return {"success": False, "error": str(e)}
    except ValueError as e:
        return {"success": False, "error": f"Ungültige Argumente: {e}"}


BUILTIN_TOOLS: dict[str, dict] = {
    "web_search": {
        "description": (
            "Web-Suche via SearXNG. Args: query (str, required), max_results (int, default 5). "
            "Liefert Liste von {title, url, snippet, engine}."
        ),
        "handler": _builtin_web_search,
    },
    "web_fetch": {
        "description": (
            "Lädt eine URL und extrahiert den Hauptinhalt (boilerplate-frei). "
            "Args: url (str, required), max_chars (int, default 8000). "
            "Liefert {url, title, text, truncated}."
        ),
        "handler": _builtin_web_fetch,
    },
}


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


def run_mcp_tool(mcp_tool_name: str, args: dict | None = None) -> dict:
    """Route a prefixed MCP tool name (mcp__server__tool) to the right MCP server."""
    from kern.mcp_client import call_tool
    from kern.exceptions import MCPError

    # Strip prefix, split into server + tool
    without_prefix = mcp_tool_name[len(MCP_PREFIX):]
    parts = without_prefix.split("__", 1)
    if len(parts) != 2:
        return {"success": False, "error": f"Ungültiger MCP-Tool-Name: {mcp_tool_name!r}"}

    server_name, tool_name = parts
    server = next((s for s in list_mcp_servers() if s["name"] == server_name), None)
    if not server:
        return {"success": False, "error": f"MCP-Server '{server_name}' nicht gefunden"}
    if not server["enabled"]:
        return {"success": False, "error": f"MCP-Server '{server_name}' ist deaktiviert"}

    extra_headers = json.loads(server.get("headers", "{}")) or None
    try:
        return call_tool(server["url"], tool_name, args or {}, extra_headers)
    except MCPError as e:
        log.error("MCP tool call failed [%s/%s]: %s", server_name, tool_name, e)
        return {"success": False, "error": str(e)}


def run_tool(name: str, args: dict | None = None) -> dict:
    if name.startswith(MCP_PREFIX):
        return run_mcp_tool(name, args)

    # Builtins take precedence — cannot be shadowed by user-built tools
    if name in BUILTIN_TOOLS:
        try:
            result = BUILTIN_TOOLS[name]["handler"](args or {})
            log.info("Builtin tool executed: %s (args=%s)", name, args)
            return result
        except Exception as e:
            log.exception("Builtin tool failed [%s]", name)
            return {"success": False, "error": str(e)}

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
    from kern.mcp_client import load_all_servers

    lines: list[str] = []

    if BUILTIN_TOOLS:
        lines.append("## Builtin Tools\n")
        for name, spec in BUILTIN_TOOLS.items():
            lines.append(f"- **{name}**: {spec['description']}")
        lines.append("")

    local = list_tools()
    if local:
        lines.append("## Lokale Tools\n")
        for t in local:
            lines.append(f"- **{t['name']}**: {t['description']} (genutzt: {t['usage_count']}x)")

    try:
        mcp_tools = load_all_servers()
    except Exception as e:
        log.warning("MCP tool load failed: %s", e)
        mcp_tools = []

    if mcp_tools:
        lines.append("\n## MCP Tools\n")
        for t in mcp_tools:
            mcp_name = f"{MCP_PREFIX}{t['server']}__{t['name']}"
            lines.append(f"- **{mcp_name}**: {t['description']}")

    return "\n".join(lines)
