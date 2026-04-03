"""Tests for kern.tools — tool registry, execution, manifest."""
import pytest
from unittest.mock import patch
from pathlib import Path


class TestRegisterTool:
    def test_register_new_tool(self, db_path):
        from kern.tools import register_tool, get_tool
        register_tool("test_tool", "A test tool", "/tmp/test.py")
        tool = get_tool("test_tool")
        assert tool is not None
        assert tool["name"] == "test_tool"
        assert tool["description"] == "A test tool"

    def test_upsert_existing(self, db_path):
        from kern.tools import register_tool, get_tool
        register_tool("tool", "v1", "/tmp/v1.py")
        register_tool("tool", "v2", "/tmp/v2.py")
        tool = get_tool("tool")
        assert tool["description"] == "v2"
        assert tool["script_path"] == "/tmp/v2.py"


class TestGetTool:
    def test_nonexistent(self, db_path):
        from kern.tools import get_tool
        assert get_tool("nonexistent") is None


class TestListTools:
    def test_empty(self, db_path):
        from kern.tools import list_tools
        assert list_tools() == []

    def test_lists_all(self, db_path):
        from kern.tools import register_tool, list_tools
        register_tool("a", "Tool A", "/a.py")
        register_tool("b", "Tool B", "/b.py")
        tools = list_tools()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"a", "b"}


class TestRunTool:
    def test_tool_not_found(self, db_path):
        from kern.tools import run_tool
        result = run_tool("nonexistent")
        assert result["success"] is False
        assert "nicht gefunden" in result["error"]

    def test_script_not_found(self, db_path):
        from kern.tools import register_tool, run_tool
        register_tool("ghost", "Ghost tool", "/nonexistent/ghost.py")
        result = run_tool("ghost")
        assert result["success"] is False
        assert "Script nicht gefunden" in result["error"]

    def test_successful_execution(self, db_path, tmp_path):
        from kern.tools import register_tool, run_tool
        script = tmp_path / "hello.py"
        script.write_text(
            "def main(args):\n"
            "    return {'success': True, 'result': f'Hello {args.get(\"name\", \"World\")}'}\n"
        )
        register_tool("hello", "Greet", str(script))
        result = run_tool("hello", {"name": "Maik"})
        assert result["success"] is True
        assert "Maik" in result["result"]

    def test_execution_error(self, db_path, tmp_path):
        from kern.tools import register_tool, run_tool
        script = tmp_path / "broken.py"
        script.write_text(
            "def main(args):\n"
            "    raise ValueError('broken')\n"
        )
        register_tool("broken", "Broken tool", str(script))
        result = run_tool("broken")
        assert result["success"] is False
        assert "broken" in result["error"]

    def test_usage_count_incremented(self, db_path, tmp_path):
        from kern.tools import register_tool, run_tool, get_tool
        script = tmp_path / "counter.py"
        script.write_text("def main(args): return {'success': True}\n")
        register_tool("counter", "Counter", str(script))
        run_tool("counter")
        run_tool("counter")
        tool = get_tool("counter")
        assert tool["usage_count"] == 2


class TestSaveToolScript:
    def test_creates_file(self, tmp_path):
        from kern.tools import save_tool_script, TOOLS_DIR
        with patch("kern.tools.TOOLS_DIR", tmp_path / "tools"):
            from kern.tools import save_tool_script
            import kern.tools
            old = kern.tools.TOOLS_DIR
            kern.tools.TOOLS_DIR = tmp_path / "tools"
            try:
                path = kern.tools.save_tool_script("test", "print('hello')")
                assert Path(path).exists()
                assert Path(path).read_text() == "print('hello')"
            finally:
                kern.tools.TOOLS_DIR = old


class TestBuildToolsManifest:
    def test_empty(self, db_path):
        from kern.tools import build_tools_manifest
        assert build_tools_manifest() == ""

    def test_with_tools(self, db_path):
        from kern.tools import register_tool, build_tools_manifest
        register_tool("btc", "Bitcoin Kurs abfragen", "/tools/btc.py")
        manifest = build_tools_manifest()
        assert "btc" in manifest
        assert "Bitcoin" in manifest
        assert "Verfügbare Tools" in manifest
