"""Tests for kern.tools — tool registry, execution, manifest, security."""
import pytest
from pathlib import Path
from unittest.mock import patch


class TestToolNameValidation:
    def test_valid_names(self):
        from kern.tools import _validate_tool_name
        for name in ["btc", "weather_api", "tool-123", "MyTool"]:
            _validate_tool_name(name)  # Should not raise

    def test_path_traversal_rejected(self):
        from kern.tools import _validate_tool_name
        from kern.exceptions import ToolSecurityError
        for name in ["../evil", "../../etc/cron", "foo/bar", "evil.py"]:
            with pytest.raises(ToolSecurityError):
                _validate_tool_name(name)

    def test_empty_name_rejected(self):
        from kern.tools import _validate_tool_name
        from kern.exceptions import ToolSecurityError
        with pytest.raises(ToolSecurityError):
            _validate_tool_name("")

    def test_special_chars_rejected(self):
        from kern.tools import _validate_tool_name
        from kern.exceptions import ToolSecurityError
        for name in ["rm -rf", "foo;bar", "tool$(cmd)"]:
            with pytest.raises(ToolSecurityError):
                _validate_tool_name(name)


class TestScriptPathValidation:
    def test_valid_path(self, tmp_path):
        import kern.tools
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            kern.tools._validate_script_path(str(tmp_path / "tools" / "test.py"))
        finally:
            kern.tools.TOOLS_DIR = old

    def test_path_traversal_rejected(self, tmp_path):
        import kern.tools
        from kern.exceptions import ToolSecurityError
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            with pytest.raises(ToolSecurityError):
                kern.tools._validate_script_path("/etc/evil.py")
            with pytest.raises(ToolSecurityError):
                kern.tools._validate_script_path(str(tmp_path / "evil.py"))
        finally:
            kern.tools.TOOLS_DIR = old


class TestRegisterTool:
    def test_register_new_tool(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, get_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "test_tool.py"
            script.write_text("def main(args): pass\n")
            register_tool("test_tool", "A test tool", str(script))
            tool = get_tool("test_tool")
            assert tool is not None
            assert tool["name"] == "test_tool"
            assert tool["description"] == "A test tool"
        finally:
            kern.tools.TOOLS_DIR = old

    def test_upsert_existing(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, get_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            s1 = tmp_path / "tools" / "tool.py"
            s1.write_text("pass\n")
            register_tool("tool", "v1", str(s1))
            register_tool("tool", "v2", str(s1))
            tool = get_tool("tool")
            assert tool["description"] == "v2"
        finally:
            kern.tools.TOOLS_DIR = old

    def test_register_rejects_path_traversal(self, db_path):
        from kern.tools import register_tool
        from kern.exceptions import ToolSecurityError
        with pytest.raises(ToolSecurityError):
            register_tool("evil", "Evil", "/etc/cron.d/evil.py")


class TestGetTool:
    def test_nonexistent(self, db_path):
        from kern.tools import get_tool
        assert get_tool("nonexistent") is None


class TestListTools:
    def test_empty(self, db_path):
        from kern.tools import list_tools
        assert list_tools() == []

    def test_lists_all(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, list_tools
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            for name in ("a", "b"):
                s = tmp_path / "tools" / f"{name}.py"
                s.write_text("pass\n")
                register_tool(name, f"Tool {name}", str(s))
            tools = list_tools()
            assert len(tools) == 2
            names = {t["name"] for t in tools}
            assert names == {"a", "b"}
        finally:
            kern.tools.TOOLS_DIR = old


class TestRunTool:
    def test_tool_not_found(self, db_path):
        from kern.tools import run_tool
        result = run_tool("nonexistent")
        assert result["success"] is False
        assert "nicht gefunden" in result["error"]

    def test_script_not_found(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, run_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            ghost_path = tmp_path / "tools" / "ghost.py"
            # Register with a valid path pattern but file doesn't exist
            register_tool("ghost", "Ghost tool", str(ghost_path))
            result = run_tool("ghost")
            assert result["success"] is False
            assert "Script nicht gefunden" in result["error"]
        finally:
            kern.tools.TOOLS_DIR = old

    def test_successful_execution(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, run_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "hello.py"
            script.write_text(
                "def main(args):\n"
                "    return {'success': True, 'result': f'Hello {args.get(\"name\", \"World\")}'}\n"
            )
            register_tool("hello", "Greet", str(script))
            result = run_tool("hello", {"name": "Maik"})
            assert result["success"] is True
            assert "Maik" in result["result"]
        finally:
            kern.tools.TOOLS_DIR = old

    def test_execution_error(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, run_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "broken.py"
            script.write_text(
                "def main(args):\n"
                "    raise ValueError('broken')\n"
            )
            register_tool("broken", "Broken tool", str(script))
            result = run_tool("broken")
            assert result["success"] is False
            assert "broken" in result["error"]
        finally:
            kern.tools.TOOLS_DIR = old

    def test_missing_main_function(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, run_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "no_main.py"
            script.write_text("x = 42\n")
            register_tool("no_main", "No main", str(script))
            result = run_tool("no_main")
            assert result["success"] is False
            assert "main" in result["error"]
        finally:
            kern.tools.TOOLS_DIR = old

    def test_usage_count_incremented(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, run_tool, get_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "counter.py"
            script.write_text("def main(args): return {'success': True}\n")
            register_tool("counter", "Counter", str(script))
            run_tool("counter")
            run_tool("counter")
            tool = get_tool("counter")
            assert tool["usage_count"] == 2
        finally:
            kern.tools.TOOLS_DIR = old

    def test_none_args_defaults_to_empty_dict(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, run_tool
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "argstest.py"
            script.write_text("def main(args): return {'success': True, 'result': str(args)}\n")
            register_tool("argstest", "Args test", str(script))
            result = run_tool("argstest", None)
            assert result["success"] is True
            assert "{}" in result["result"]
        finally:
            kern.tools.TOOLS_DIR = old


class TestSaveToolScript:
    def test_creates_file(self, tmp_path):
        import kern.tools
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        try:
            path = kern.tools.save_tool_script("test", "print('hello')")
            assert Path(path).exists()
            assert Path(path).read_text() == "print('hello')"
        finally:
            kern.tools.TOOLS_DIR = old

    def test_rejects_path_traversal_name(self, tmp_path):
        import kern.tools
        from kern.exceptions import ToolSecurityError
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        try:
            with pytest.raises(ToolSecurityError):
                kern.tools.save_tool_script("../../evil", "import os")
        finally:
            kern.tools.TOOLS_DIR = old


class TestBuildToolsManifest:
    def test_empty(self, db_path):
        from kern.tools import build_tools_manifest
        assert build_tools_manifest() == ""

    def test_with_tools(self, db_path, tmp_path):
        import kern.tools
        from kern.tools import register_tool, build_tools_manifest
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "btc.py"
            script.write_text("pass\n")
            register_tool("btc", "Bitcoin Kurs abfragen", str(script))
            manifest = build_tools_manifest()
            assert "btc" in manifest
            assert "Bitcoin" in manifest
            assert "Lokale Tools" in manifest
        finally:
            kern.tools.TOOLS_DIR = old
