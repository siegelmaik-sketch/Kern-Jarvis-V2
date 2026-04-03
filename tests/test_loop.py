"""Tests for kern.loop — commands, config, bg message queue."""
import queue
import pytest
from unittest.mock import patch, MagicMock


class TestPrintConfig:
    def test_show_all(self, db_path, capsys):
        from kern.db import set_config
        from kern.loop import print_config
        set_config("llm_provider", "anthropic")
        set_config("llm_model", "claude-sonnet-4-6")
        print_config("")
        output = capsys.readouterr().out
        assert "llm_provider" in output
        assert "llm_model" in output

    def test_set_valid_key(self, db_path, capsys):
        from kern.loop import print_config
        from kern.db import get_config
        print_config("set llm_model gpt-4o")
        assert get_config("llm_model") == "gpt-4o"

    def test_set_invalid_key(self, db_path, capsys):
        from kern.loop import print_config
        print_config("set invalid_key value")
        output = capsys.readouterr().out
        assert "Unbekannter Key" in output

    def test_get_key(self, db_path, capsys):
        from kern.db import set_config
        from kern.loop import print_config
        set_config("user_name", "Maik")
        print_config("get user_name")
        output = capsys.readouterr().out
        assert "Maik" in output

    def test_api_key_masked_on_display(self, db_path, capsys):
        from kern.db import set_config
        from kern.loop import print_config
        set_config("llm_api_key", "sk-ant-api03-verysecretkey123")
        print_config("")
        output = capsys.readouterr().out
        assert "verysecretkey123" not in output
        assert "***" in output

    def test_api_key_masked_on_set(self, db_path, capsys):
        """API key should be masked when echoing back after set."""
        from kern.loop import print_config
        print_config("set llm_api_key sk-ant-api03-secretkey789")
        output = capsys.readouterr().out
        assert "secretkey789" not in output
        assert "***" in output

    def test_api_key_masked_on_get(self, db_path, capsys):
        from kern.db import set_config
        from kern.loop import print_config
        set_config("llm_api_key", "sk-ant-api03-verysecretkey123")
        print_config("get llm_api_key")
        output = capsys.readouterr().out
        assert "verysecretkey123" not in output
        assert "***" in output

    def test_config_set_invalidates_client_cache(self, db_path, capsys):
        from kern.loop import print_config
        with patch("kern.loop.invalidate_client_cache") as mock_invalidate:
            print_config("set llm_api_key test-key-12345")
            mock_invalidate.assert_called_once()


class TestPrintTools:
    def test_no_tools(self, db_path, capsys):
        from kern.loop import print_tools
        print_tools()
        output = capsys.readouterr().out
        assert "keine Tools" in output

    def test_with_tools(self, db_path, capsys, tmp_path):
        from kern.tools import register_tool, TOOLS_DIR
        import kern.tools
        old = kern.tools.TOOLS_DIR
        kern.tools.TOOLS_DIR = tmp_path / "tools"
        (tmp_path / "tools").mkdir()
        try:
            script = tmp_path / "tools" / "btc.py"
            script.write_text("def main(args): pass\n")
            register_tool("btc", "Bitcoin Kurs", str(script))
            from kern.loop import print_tools
            print_tools()
            output = capsys.readouterr().out
            assert "btc" in output
            assert "1 Tool" in output
        finally:
            kern.tools.TOOLS_DIR = old


class TestPrintMemory:
    def test_empty_memory(self, db_path, capsys):
        from kern.loop import print_memory
        print_memory()
        output = capsys.readouterr().out
        assert "leer" in output

    def test_with_facts(self, db_path, capsys, mock_embedding):
        from kern.memory import save_fact
        from kern.loop import print_memory
        save_fact("User heißt Maik", category="preference", source="user", importance=9)
        print_memory()
        output = capsys.readouterr().out
        assert "Maik" in output
        assert "1 Fakt" in output


class TestPrintSearch:
    def test_empty_query(self, db_path, capsys):
        from kern.loop import print_search
        print_search("")
        output = capsys.readouterr().out
        assert "Verwendung" in output

    def test_no_results(self, db_path, capsys, mock_embedding):
        from kern.loop import print_search
        with patch("kern.memory._get_embedding", return_value=None):
            print_search("nonexistent")
            output = capsys.readouterr().out
            # Falls back to get_facts which is empty
            assert "Keine Ergebnisse" in output or output.strip() == ""


class TestBgMessages:
    def test_flush_empty_queue(self, capsys):
        from kern.loop import _flush_bg_messages, _bg_messages
        while not _bg_messages.empty():
            _bg_messages.get_nowait()
        _flush_bg_messages()
        output = capsys.readouterr().out
        assert output == ""

    def test_flush_with_messages(self, capsys):
        from kern.loop import _flush_bg_messages, _bg_messages
        while not _bg_messages.empty():
            _bg_messages.get_nowait()
        _bg_messages.put("  [Memory: 2 Fakt(en) gelernt]")
        _bg_messages.put("  [Memory: 1 Fakt(en) gelernt]")
        _flush_bg_messages()
        output = capsys.readouterr().out
        assert "2 Fakt" in output
        assert "1 Fakt" in output


class TestRunImplicitMemory:
    def test_logs_error_instead_of_silencing(self, db_path):
        """Errors should be logged, not silenced with bare except:pass."""
        from kern.loop import _run_implicit_memory
        with patch("kern.loop.extract_from_conversation", side_effect=Exception("boom")):
            with patch("kern.loop.log") as mock_log:
                _run_implicit_memory("test", "test")
                mock_log.warning.assert_called_once()

    def test_queues_message_on_success(self, db_path, mock_embedding):
        from kern.loop import _run_implicit_memory, _bg_messages

        items = [{"type": "todo", "content": "Test", "confidence": 0.9, "importance": 7}]
        with patch("kern.loop.extract_from_conversation", return_value=items):
            while not _bg_messages.empty():
                _bg_messages.get_nowait()
            _run_implicit_memory("test " * 50, "response " * 50)
            assert not _bg_messages.empty()
            msg = _bg_messages.get_nowait()
            assert "1 Fakt" in msg
