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

    def test_api_key_masked(self, db_path, capsys):
        from kern.db import set_config
        from kern.loop import print_config
        set_config("llm_api_key", "sk-ant-api03-verysecretkey123")
        print_config("")
        output = capsys.readouterr().out
        assert "verysecretkey123" not in output
        assert "***" in output


class TestPrintTools:
    def test_no_tools(self, db_path, capsys):
        from kern.loop import print_tools
        print_tools()
        output = capsys.readouterr().out
        assert "keine Tools" in output

    def test_with_tools(self, db_path, capsys):
        from kern.tools import register_tool
        from kern.loop import print_tools
        register_tool("btc", "Bitcoin Kurs", "/tools/btc.py")
        print_tools()
        output = capsys.readouterr().out
        assert "btc" in output
        assert "1 Tool" in output


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
            # Falls back to get_facts which is empty
            output = capsys.readouterr().out


class TestBgMessages:
    def test_flush_empty_queue(self, capsys):
        from kern.loop import _flush_bg_messages, _bg_messages
        # Clear queue
        while not _bg_messages.empty():
            _bg_messages.get_nowait()
        _flush_bg_messages()
        output = capsys.readouterr().out
        assert output == ""

    def test_flush_with_messages(self, capsys):
        from kern.loop import _flush_bg_messages, _bg_messages
        # Clear queue
        while not _bg_messages.empty():
            _bg_messages.get_nowait()
        _bg_messages.put("  [Memory: 2 Fakt(en) gelernt]")
        _bg_messages.put("  [Memory: 1 Fakt(en) gelernt]")
        _flush_bg_messages()
        output = capsys.readouterr().out
        assert "2 Fakt" in output
        assert "1 Fakt" in output


class TestRunImplicitMemory:
    def test_no_crash_on_error(self, db_path):
        from kern.loop import _run_implicit_memory
        with patch("kern.implicit_memory.extract_from_conversation", side_effect=Exception("boom")):
            # Should not raise
            _run_implicit_memory("test", "test")

    def test_queues_message_on_success(self, db_path, mock_embedding):
        from kern.loop import _run_implicit_memory, _bg_messages

        items = [{"type": "todo", "content": "Test", "confidence": 0.9, "importance": 7}]
        with patch("kern.loop.extract_from_conversation", return_value=items):
            # Clear queue
            while not _bg_messages.empty():
                _bg_messages.get_nowait()
            _run_implicit_memory("test " * 50, "response " * 50)
            assert not _bg_messages.empty()
            msg = _bg_messages.get_nowait()
            assert "1 Fakt" in msg
