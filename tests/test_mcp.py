"""Tests for MCP client and MCP server registry."""
import json
import pytest
from unittest.mock import patch, MagicMock


# ── DB / Registry Tests ───────────────────────────────────────────────────────

class TestMCPServerRegistry:
    def test_add_and_get_server(self, db_path):
        from kern.db import add_mcp_server, get_mcp_server
        add_mcp_server("test_srv", "http://localhost:9000")
        srv = get_mcp_server("test_srv")
        assert srv is not None
        assert srv["url"] == "http://localhost:9000"
        assert srv["enabled"] == 1

    def test_add_server_with_headers(self, db_path):
        from kern.db import add_mcp_server, get_mcp_server
        add_mcp_server("hdr_srv", "http://localhost:9001", {"Authorization": "Bearer tok"})
        srv = get_mcp_server("hdr_srv")
        headers = json.loads(srv["headers"])
        assert headers["Authorization"] == "Bearer tok"

    def test_upsert_updates_url(self, db_path):
        from kern.db import add_mcp_server, get_mcp_server
        add_mcp_server("up_srv", "http://old-url")
        add_mcp_server("up_srv", "http://new-url")
        srv = get_mcp_server("up_srv")
        assert srv["url"] == "http://new-url"

    def test_remove_returns_true_on_success(self, db_path):
        from kern.db import add_mcp_server, remove_mcp_server
        add_mcp_server("del_srv", "http://localhost:9002")
        assert remove_mcp_server("del_srv") is True

    def test_remove_returns_false_if_not_found(self, db_path):
        from kern.db import remove_mcp_server
        assert remove_mcp_server("nonexistent") is False

    def test_list_mcp_servers(self, db_path):
        from kern.db import add_mcp_server, list_mcp_servers
        add_mcp_server("list_a", "http://a")
        add_mcp_server("list_b", "http://b")
        servers = list_mcp_servers()
        names = [s["name"] for s in servers]
        assert "list_a" in names
        assert "list_b" in names

    def test_get_nonexistent_server_returns_none(self, db_path):
        from kern.db import get_mcp_server
        assert get_mcp_server("does_not_exist") is None


# ── MCP Client Unit Tests ────────────────────────────────────────────────────

class TestMCPClientHelpers:
    def test_jsonrpc_format(self):
        from kern.mcp_client import _jsonrpc
        result = _jsonrpc("tools/list", req_id=5)
        assert result["jsonrpc"] == "2.0"
        assert result["method"] == "tools/list"
        assert result["id"] == 5
        assert "params" not in result

    def test_jsonrpc_with_params(self):
        from kern.mcp_client import _jsonrpc
        result = _jsonrpc("tools/call", {"name": "foo", "arguments": {}}, req_id=2)
        assert result["params"]["name"] == "foo"

    def test_parse_sse_extracts_data(self):
        from kern.mcp_client import _parse_sse
        sse = 'event: message\ndata: {"jsonrpc":"2.0","result":{"tools":[]},"id":1}\n\n'
        parsed = _parse_sse(sse)
        assert parsed["result"]["tools"] == []

    def test_parse_sse_raises_on_empty(self):
        from kern.mcp_client import _parse_sse
        from kern.exceptions import MCPError
        with pytest.raises(MCPError):
            _parse_sse("event: ping\n\n")

    def test_invalidate_cache_clears_entry(self):
        from kern.mcp_client import _tool_cache, _tool_cache_lock, invalidate_cache
        with _tool_cache_lock:
            _tool_cache["my_server"] = [{"name": "foo"}]
        invalidate_cache("my_server")
        with _tool_cache_lock:
            assert "my_server" not in _tool_cache

    def test_invalidate_cache_clears_all(self):
        from kern.mcp_client import _tool_cache, _tool_cache_lock, invalidate_cache
        with _tool_cache_lock:
            _tool_cache["srv1"] = []
            _tool_cache["srv2"] = []
        invalidate_cache(None)
        with _tool_cache_lock:
            assert len(_tool_cache) == 0

    def test_get_cached_tools_returns_empty_for_unknown(self):
        from kern.mcp_client import get_cached_tools
        assert get_cached_tools("unknown_srv") == []

    def test_get_cached_tools_returns_cached(self):
        from kern.mcp_client import _tool_cache, _tool_cache_lock, get_cached_tools, invalidate_cache
        with _tool_cache_lock:
            _tool_cache["cached_srv"] = [{"name": "mytool"}]
        result = get_cached_tools("cached_srv")
        assert result[0]["name"] == "mytool"
        invalidate_cache("cached_srv")


class TestFetchTools:
    def test_fetch_tools_returns_normalized_list(self):
        from kern.mcp_client import fetch_tools, _sessions, _sessions_lock, invalidate_cache

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "tools": [
                    {"name": "calculator", "description": "Does math", "inputSchema": {"type": "object"}},
                    {"name": "weather", "description": "Gets weather"},
                ]
            },
            "id": 2
        }

        with _sessions_lock:
            _sessions["http://mock-server"] = "sess-123"

        with patch("httpx.post", return_value=mock_response):
            tools = fetch_tools("mock", "http://mock-server")

        assert len(tools) == 2
        assert tools[0]["name"] == "calculator"
        assert tools[0]["server"] == "mock"
        assert tools[1]["description"] == "Gets weather"

        invalidate_cache("mock")

    def test_fetch_tools_raises_mcp_error_on_http_failure(self):
        from kern.mcp_client import fetch_tools, _sessions, _sessions_lock
        from kern.exceptions import MCPError
        import httpx as _httpx

        with _sessions_lock:
            _sessions["http://bad-server"] = "x"

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("httpx.post", side_effect=_httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_resp
        )):
            with pytest.raises(MCPError):
                fetch_tools("bad", "http://bad-server")


class TestCallTool:
    def test_call_tool_returns_text_result(self):
        from kern.mcp_client import call_tool, _sessions, _sessions_lock, invalidate_cache

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "content": [{"type": "text", "text": "42"}],
                "isError": False,
            },
            "id": 3
        }

        with _sessions_lock:
            _sessions["http://tool-server"] = "sess"

        with patch("httpx.post", return_value=mock_response):
            result = call_tool("http://tool-server", "calculator", {"a": 6, "b": 7})

        assert result["success"] is True
        assert result["result"] == 42  # parsed from JSON

        invalidate_cache()

    def test_call_tool_returns_error_on_is_error(self):
        from kern.mcp_client import call_tool, _sessions, _sessions_lock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "content": [{"type": "text", "text": "division by zero"}],
                "isError": True,
            },
            "id": 3
        }

        with _sessions_lock:
            _sessions["http://err-server"] = "sess"

        with patch("httpx.post", return_value=mock_response):
            result = call_tool("http://err-server", "divide", {"a": 1, "b": 0})

        assert result["success"] is False
        assert "division by zero" in result["error"]


# ── run_tool MCP routing Tests ────────────────────────────────────────────────

class TestRunToolMCPRouting:
    def test_mcp_prefix_routes_to_mcp(self, db_path):
        from kern.tools import run_tool
        from kern.db import add_mcp_server

        add_mcp_server("route_srv", "http://route")

        with patch("kern.tools.run_mcp_tool", return_value={"success": True, "result": "ok"}) as mock_mcp:
            result = run_tool("mcp__route_srv__some_tool", {"x": 1})

        mock_mcp.assert_called_once_with("mcp__route_srv__some_tool", {"x": 1})
        assert result["success"] is True

    def test_invalid_mcp_name_returns_error(self, db_path):
        from kern.tools import run_mcp_tool
        result = run_mcp_tool("mcp__no_double_underscore", {})
        assert result["success"] is False
        assert "Ungültiger" in result["error"]

    def test_unknown_server_returns_error(self, db_path):
        from kern.tools import run_mcp_tool
        result = run_mcp_tool("mcp__ghost_server__tool", {})
        assert result["success"] is False
        assert "ghost_server" in result["error"]
