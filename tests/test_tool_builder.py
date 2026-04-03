"""Tests for kern.tool_builder — command parsing, code extraction, execution."""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestExtractCodeBlock:
    def test_python_block(self):
        from kern.tool_builder import extract_code_block
        text = "Here is the code:\n```python\ndef main(): pass\n```"
        assert extract_code_block(text) == "def main(): pass"

    def test_plain_block(self):
        from kern.tool_builder import extract_code_block
        text = "```\nprint('hi')\n```"
        assert extract_code_block(text) == "print('hi')"

    def test_no_block(self):
        from kern.tool_builder import extract_code_block
        assert extract_code_block("just text") is None

    def test_multiline(self):
        from kern.tool_builder import extract_code_block
        text = '```python\nimport os\n\ndef main(args):\n    return {"success": True}\n```'
        code = extract_code_block(text)
        assert "import os" in code
        assert "def main" in code


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
        assert cmds[0]["key"] == "name"

    def test_memory_search(self):
        from kern.tool_builder import parse_jarvis_commands
        text = "MEMORY_SEARCH(query='bitcoin preis')"
        cmds = parse_jarvis_commands(text)
        assert len(cmds) == 1
        assert cmds[0]["type"] == "memory_search"
        assert cmds[0]["query"] == "bitcoin preis"

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
        from kern.tools import register_tool
        script = tmp_path / "simple.py"
        script.write_text("def main(args): return {'success': True, 'result': 'ok'}\n")
        register_tool("simple", "Simple tool", str(script))
        results = execute_commands([{
            "type": "run_tool",
            "name": "simple",
            "args": {},
        }])
        assert results[0]["success"] is True

    def test_register_tool(self, db_path):
        from kern.tool_builder import execute_commands
        from kern.tools import get_tool
        results = execute_commands([{
            "type": "register_tool",
            "name": "new_tool",
            "description": "A new tool",
            "script_path": "/tools/new_tool.py",
        }])
        assert results[0]["success"] is True
        assert get_tool("new_tool") is not None


class TestRunToolSafe:
    def test_successful_execution(self):
        from kern.tool_builder import run_tool_temp
        code = "def main(args): return {'success': True, 'result': 'hello'}\n"
        result = run_tool_temp("test", code, {})
        assert result["success"] is True
        assert result["result"] == "hello"

    def test_execution_error(self):
        from kern.tool_builder import run_tool_temp
        code = "def main(args): raise RuntimeError('boom')\n"
        result = run_tool_temp("test", code, {})
        assert result["success"] is False
        assert "boom" in result["error"]

    def test_syntax_error(self):
        from kern.tool_builder import run_tool_temp
        code = "def main(args):\n    +++invalid syntax\n"
        result = run_tool_temp("test", code, {})
        assert result["success"] is False
