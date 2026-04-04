"""
Kern-Jarvis V2 — MCP Client (Model Context Protocol)

Connects to MCP servers via Streamable HTTP transport (JSON-RPC 2.0).
Discovers tools and executes them as native Jarvis tools.
"""
import json
import logging
import threading
from typing import Any

import httpx

from kern.exceptions import MCPError

log = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT = 10.0

# ── Session Cache ─────────────────────────────────────────────────────────────
# Keyed by server URL → session_id (may be None for stateless servers)
_sessions: dict[str, str | None] = {}
_sessions_lock = threading.Lock()

# ── Tool Cache ────────────────────────────────────────────────────────────────
# Keyed by server name → list of tool dicts
_tool_cache: dict[str, list[dict]] = {}
_tool_cache_lock = threading.Lock()


def _jsonrpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params is not None:
        payload["params"] = params
    return payload


def _post(url: str, payload: dict, extra_headers: dict | None = None) -> tuple[dict, str | None]:
    """POST JSON-RPC payload to MCP server. Returns (response_dict, session_id)."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if extra_headers:
        headers.update(extra_headers)

    session_id: str | None = None
    with _sessions_lock:
        session_id = _sessions.get(url)
    if session_id:
        headers["mcp-session-id"] = session_id

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise MCPError(f"HTTP {e.response.status_code} von {url}: {e.response.text[:200]}") from e
    except httpx.RequestError as e:
        raise MCPError(f"Verbindungsfehler zu {url}: {e}") from e

    new_session_id = resp.headers.get("mcp-session-id")
    if new_session_id:
        with _sessions_lock:
            _sessions[url] = new_session_id

    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        # Parse SSE: extract the first data line
        data = _parse_sse(resp.text)
    else:
        data = resp.json()

    if "error" in data:
        err = data["error"]
        raise MCPError(f"MCP-Fehler [{err.get('code')}]: {err.get('message', 'unknown')}")

    return data, new_session_id


def _parse_sse(text: str) -> dict:
    """Extract JSON from the first data: line in an SSE response."""
    for line in text.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw and raw != "[DONE]":
                return json.loads(raw)
    raise MCPError("Leere SSE-Antwort vom MCP-Server")


def _initialize(url: str, extra_headers: dict | None = None) -> None:
    """Perform MCP initialize handshake if not already done for this URL."""
    with _sessions_lock:
        if url in _sessions:
            return

    payload = _jsonrpc("initialize", {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "clientInfo": {"name": "kern-jarvis", "version": "2.0"},
    })
    try:
        response, _ = _post(url, payload, extra_headers)
        # Mark as initialized even if no session_id returned (stateless server)
        with _sessions_lock:
            if url not in _sessions:
                _sessions[url] = None

        # Send initialized notification (fire-and-forget, no response expected)
        notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            httpx.post(url, json=notify, headers=_build_headers(url, extra_headers), timeout=5.0)
        except Exception:
            pass  # Notification failures are non-fatal

        log.info("MCP initialized: %s (protocol=%s)",
                 url, response.get("result", {}).get("protocolVersion", "?"))
    except MCPError:
        # Some servers skip initialize — mark as done anyway so we don't retry forever
        with _sessions_lock:
            _sessions[url] = None
        log.debug("MCP initialize skipped/failed for %s, continuing anyway", url)


def _build_headers(url: str, extra_headers: dict | None) -> dict:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if extra_headers:
        headers.update(extra_headers)
    with _sessions_lock:
        sid = _sessions.get(url)
    if sid:
        headers["mcp-session-id"] = sid
    return headers


def fetch_tools(server_name: str, url: str, extra_headers: dict | None = None) -> list[dict]:
    """Fetch tool list from MCP server. Returns list of tool metadata dicts."""
    _initialize(url, extra_headers)

    payload = _jsonrpc("tools/list", req_id=2)
    response, _ = _post(url, payload, extra_headers)
    tools_raw: list[dict] = response.get("result", {}).get("tools", [])

    tools = []
    for t in tools_raw:
        tools.append({
            "server": server_name,
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "input_schema": t.get("inputSchema", {}),
        })

    with _tool_cache_lock:
        _tool_cache[server_name] = tools

    log.info("MCP tools fetched from %s: %d tool(s)", server_name, len(tools))
    return tools


def get_cached_tools(server_name: str) -> list[dict]:
    with _tool_cache_lock:
        return _tool_cache.get(server_name, [])


def invalidate_cache(server_name: str | None = None) -> None:
    """Clear tool cache. Pass None to clear all."""
    with _tool_cache_lock:
        if server_name is None:
            _tool_cache.clear()
        else:
            _tool_cache.pop(server_name, None)
    if server_name is None:
        with _sessions_lock:
            _sessions.clear()
    else:
        # Can't easily find URL from name here — full reset is safe
        pass


def call_tool(url: str, tool_name: str, arguments: dict, extra_headers: dict | None = None) -> dict:
    """Call a tool on an MCP server. Returns the tool result."""
    _initialize(url, extra_headers)

    payload = _jsonrpc("tools/call", {
        "name": tool_name,
        "arguments": arguments,
    }, req_id=3)

    response, _ = _post(url, payload, extra_headers)
    result = response.get("result", {})

    # MCP tool results: list of content items
    content = result.get("content", [])
    is_error = result.get("isError", False)

    if is_error:
        error_text = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
        return {"success": False, "error": error_text or "MCP tool returned an error"}

    # Flatten text content into a single result string
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    combined = "\n".join(texts).strip()

    # Try to parse JSON result if possible
    try:
        parsed = json.loads(combined)
        return {"success": True, "result": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"success": True, "result": combined}


def load_all_servers() -> list[dict]:
    """Load tool lists from all enabled MCP servers. Returns merged tool list."""
    from kern.db import list_mcp_servers

    servers = [s for s in list_mcp_servers() if s["enabled"]]
    all_tools = []

    for server in servers:
        extra_headers = json.loads(server.get("headers", "{}"))
        try:
            tools = fetch_tools(server["name"], server["url"], extra_headers or None)
            all_tools.extend(tools)
        except MCPError as e:
            log.warning("MCP server %s nicht erreichbar: %s", server["name"], e)

    return all_tools
