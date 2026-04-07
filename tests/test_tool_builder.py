"""Tests for kern.tool_builder — command parsing, execution dispatch."""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestParseJarvisCommands:
    def test_register_tool(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "REGISTER_TOOL(name='btc', description='Bitcoin Kurs', script_path='/tools/btc.py')"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "register_tool"
        assert cmds[0]["name"] == "btc"

    def test_build_tool(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "BUILD_TOOL(name='wetter', description='Wetter abfragen', task='Hole aktuelles Wetter')"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "build_tool"
        assert cmds[0]["name"] == "wetter"
        assert cmds[0]["task"] == "Hole aktuelles Wetter"

    def test_run_tool_without_args(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "RUN_TOOL(name='btc')"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "run_tool"
        assert cmds[0]["name"] == "btc"
        assert cmds[0]["args"] == {}

    def test_run_tool_with_args(self):
        from kern.tool_builder import parse_jarvis_commands
        text = 'RUN_TOOL(name=\'btc\', args={"currency": "EUR"})'
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["args"] == {"currency": "EUR"}

    def test_memory_save(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "MEMORY_SAVE(type='user', key='name', value='Maik')"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "memory_save"
        assert cmds[0]["key"] == "name"
        assert cmds[0]["value"] == "Maik"

    def test_memory_get(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "MEMORY_GET(key='name')"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "memory_get"

    def test_memory_search(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "MEMORY_SEARCH(query='bitcoin preis')"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "memory_search"

    def test_multiple_commands(self):
        from kern.tool_builder import parse_jarvis_commands
        text = (
            "Ich speichere das: MEMORY_SAVE(type='user', key='name', value='Maik')\n"
            "Und starte: RUN_TOOL(name='btc')"
        )
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 2
        types = {c["type"] for c in cmds}
        assert types == {"memory_save", "run_tool"}

    def test_no_commands(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "Hallo, wie kann ich dir helfen?"
        assert parse_jarvis_commands(text) == []

    def test_double_quotes(self):
        from kern.tool_builder import parse_jarvis_commands
        text = 'MEMORY_SAVE(type="user", key="name", value="Maik")'
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["value"] == "Maik"

    def test_multiline_run_tool(self):
        """LLMs often pretty-print calls across multiple lines."""
        from kern.tool_builder import parse_jarvis_commands
        text = (
            'RUN_TOOL(\n'
            '    name="web_search",\n'
            '    args={"query": "Wetter Aue morgen", "max_results": 3}\n'
            ')'
        )
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "run_tool"
        assert cmds[0]["name"] == "web_search"
        assert cmds[0]["args"]["query"] == "Wetter Aue morgen"

    def test_multiline_build_tool(self):
        from kern.tool_builder import parse_jarvis_commands
        text = (
            "BUILD_TOOL(\n"
            "    name='wetter',\n"
            "    description='Wetterbericht für Aue',\n"
            "    task='Hole das aktuelle Wetter für Aue von open-meteo.com'\n"
            ")"
        )
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "build_tool"
        assert cmds[0]["name"] == "wetter"

    def test_run_tool_with_empty_dict_args(self):
        """Llama-style models call tools with explicit empty args={}."""
        from kern.tool_builder import parse_jarvis_commands
        text = "RUN_TOOL(name='get_time', args={})"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["name"] == "get_time"
        assert cmds[0]["args"] == {}

    def test_run_tool_with_python_dict_args(self):
        """GPT-style models emit Python dict literals (single quotes) instead of JSON."""
        from kern.tool_builder import parse_jarvis_commands
        text = "RUN_TOOL(name='web_search', args={'query': 'bitcoin', 'max_results': 5})"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "run_tool"
        assert cmds[0]["args"] == {"query": "bitcoin", "max_results": 5}

    def test_run_tool_with_mixed_quotes_args(self):
        """Mixed: outer dict in Python style with nested string containing double quotes."""
        from kern.tool_builder import parse_jarvis_commands
        text = "RUN_TOOL(name='search', args={'query': \"hello world\"})"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["args"] == {"query": "hello world"}

    def test_build_tool_with_inner_quotes(self):
        """Task strings often contain Python dict literals with inner quotes."""
        from kern.tool_builder import parse_jarvis_commands
        text = (
            'BUILD_TOOL(\n'
            '    name="weather",\n'
            '    description="Wetter-Tool",\n'
            "    task=\"Erstelle ein Python-Skript. Rückgabeformat: "
            "{'success': bool, 'report': str}\"\n"
            ')'
        )
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["name"] == "weather"
        # Critical: the inner single quotes must NOT terminate the task string
        assert "Rückgabeformat" in cmds[0]["task"]
        assert "report" in cmds[0]["task"]


class TestExecuteCommands:
    def test_memory_save(self, db_path, mock_embedding):
        from kern.tool_builder import execute_commands
        results = execute_commands([{
            "type": "memory_save",
            "memory_type": "user",
            "key": "test",
            "value": "test value",
        }])
        assert len(results) == 1
        assert results[0]["success"] is True

    def test_memory_get_found(self, db_path, mock_embedding):
        from kern.tool_builder import execute_commands
        from kern.memory import memory_save
        memory_save("user", "lieblingsfarbe", "blau")
        results = execute_commands([{
            "type": "memory_get",
            "key": "lieblingsfarbe",
        }])
        assert results[0]["success"] is True
        assert "blau" in results[0]["result"]

    def test_memory_get_not_found(self, db_path):
        from kern.tool_builder import execute_commands
        results = execute_commands([{
            "type": "memory_get",
            "key": "nonexistent",
        }])
        assert results[0]["success"] is True
        assert "Kein Eintrag" in results[0]["result"]

    def test_memory_search(self, db_path, mock_embedding):
        from kern.tool_builder import execute_commands
        from kern.memory import save_fact
        save_fact("Bitcoin steht bei 50000", source="user")
        results = execute_commands([{
            "type": "memory_search",
            "query": "bitcoin",
        }])
        assert results[0]["success"] is True

    def test_run_tool(self, db_path, tmp_path):
        from kern.tool_builder import execute_commands
        from kern.tools import register_tool, TOOLS_DIR
        import kern.tools
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "simple.py"
            script.write_text("def main(args): return {'success': True, 'result': 'ok'}\n")
            register_tool("simple", "Simple tool", str(script))
            results = execute_commands([{
                "type": "run_tool",
                "name": "simple",
                "args": {},
            }])
            assert results[0]["success"] is True
        finally:
            kern.tools.TOOLS_DIR = old

    def test_register_tool(self, db_path, tmp_path):
        from kern.tool_builder import execute_commands
        from kern.tools import get_tool, TOOLS_DIR
        import kern.tools
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "new_tool.py"
            script.write_text("def main(args): pass\n")
            results = execute_commands([{
                "type": "register_tool",
                "name": "new_tool",
                "description": "A new tool",
                "script_path": str(script),
            }])
            assert results[0]["success"] is True
            assert get_tool("new_tool") is not None
        finally:
            kern.tools.TOOLS_DIR = old

    def test_security_error_caught(self, db_path):
        """ToolSecurityError in register_tool should be caught and reported."""
        from kern.tool_builder import execute_commands
        results = execute_commands([{
            "type": "register_tool",
            "name": "evil",
            "description": "Evil tool",
            "script_path": "/etc/evil.py",
        }])
        assert results[0]["success"] is False
        assert "Sicherheitsfehler" in results[0]["error"]

    def test_command_error_doesnt_abort_batch(self, db_path, mock_embedding):
        """One failed command shouldn't prevent others from running."""
        from kern.tool_builder import execute_commands
        results = execute_commands([
            {"type": "register_tool", "name": "../../evil", "description": "x", "script_path": "/x"},
            {"type": "memory_save", "memory_type": "user", "key": "k", "value": "v"},
        ])
        assert len(results) == 2
        assert results[0]["success"] is False
        assert results[1]["success"] is True


