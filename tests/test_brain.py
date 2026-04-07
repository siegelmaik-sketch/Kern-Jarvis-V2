"""Tests for kern.brain — prompt building, LLM client routing, error handling."""
import pytest
from unittest.mock import patch, MagicMock


class TestGetKernPrompt:
    def test_returns_prompt_text(self):
        from kern.brain import get_kern_prompt
        text = get_kern_prompt()
        assert "Jarvis" in text
        assert len(text) > 50

    def test_fallback_on_missing_file(self, tmp_path):
        import kern.brain
        old = kern.brain.KERN_PROMPT_PATH
        kern.brain.KERN_PROMPT_PATH = tmp_path / "nonexistent.md"
        try:
            result = kern.brain.get_kern_prompt()
            assert "Jarvis" in result
        finally:
            kern.brain.KERN_PROMPT_PATH = old


class TestBuildSystemPrompt:
    def test_basic_includes_kern_and_time(self):
        """Time fragment is now always injected, even with no memory/tools."""
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt()
            assert "KERN" in result
            assert "Aktuelle Zeit:" in result
            assert "Europe/Berlin" in result

    def test_with_memory_context(self):
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt(memory_context="MEMORY")
            assert "KERN" in result
            assert "MEMORY" in result
            assert "Aktuelle Zeit:" in result

    def test_with_tools_manifest(self):
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt(tools_manifest="TOOLS")
            assert "TOOLS" in result
            assert "Aktuelle Zeit:" in result

    def test_with_both_memory_and_tools(self):
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt(memory_context="MEM", tools_manifest="TOOLS")
            assert "MEM" in result
            assert "TOOLS" in result
            assert "Aktuelle Zeit:" in result

    def test_time_format_has_german_weekday(self):
        """Sanity check on the date string format."""
        from kern.brain import _now_berlin_context
        ctx = _now_berlin_context()
        assert "Europe/Berlin" in ctx
        # Must contain one of the German weekday names
        weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                    "Freitag", "Samstag", "Sonntag"]
        assert any(w in ctx for w in weekdays)


class TestGetModel:
    def test_default_anthropic(self, db_path):
        from kern.db import set_config
        from kern.brain import get_model
        set_config("llm_provider", "anthropic")
        result = get_model()
        assert "claude" in result

    def test_saved_model_takes_precedence(self, db_path):
        from kern.db import set_config
        from kern.brain import get_model
        set_config("llm_provider", "anthropic")
        set_config("llm_model", "my-custom-model")
        assert get_model() == "my-custom-model"

    def test_default_openai(self, db_path):
        from kern.db import set_config
        from kern.brain import get_model
        set_config("llm_provider", "openai")
        result = get_model()
        assert "gpt" in result

    def test_default_openrouter(self, db_path):
        from kern.db import set_config
        from kern.brain import get_model
        set_config("llm_provider", "openrouter")
        result = get_model()
        assert "openrouter" in get_model() or "claude" in get_model()


class TestGetLlmClient:
    def test_anthropic_provider(self, db_path):
        from kern.db import set_config
        set_config("llm_provider", "anthropic")
        set_config("llm_api_key", "test-key")
        with patch("anthropic.Anthropic") as mock_cls:
            from kern.brain import get_llm_client
            provider, client = get_llm_client()
            assert provider == "anthropic"
            mock_cls.assert_called_once_with(api_key="test-key")

    def test_openai_provider(self, db_path):
        from kern.db import set_config
        set_config("llm_provider", "openai")
        set_config("llm_api_key", "test-key")
        with patch("openai.OpenAI") as mock_cls:
            from kern.brain import get_llm_client
            provider, client = get_llm_client()
            assert provider == "openai"
            mock_cls.assert_called_once_with(api_key="test-key")

    def test_openrouter_provider(self, db_path):
        from kern.db import set_config
        set_config("llm_provider", "openrouter")
        set_config("llm_api_key", "test-key")
        with patch("openai.OpenAI") as mock_cls:
            from kern.brain import get_llm_client
            provider, client = get_llm_client()
            assert provider == "openrouter"
            mock_cls.assert_called_once_with(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1"
            )

    def test_unknown_provider_raises_config_error(self, db_path):
        from kern.db import set_config
        from kern.brain import get_llm_client
        from kern.exceptions import ConfigError
        set_config("llm_provider", "unknown_provider")
        set_config("llm_api_key", "test-key")
        with pytest.raises(ConfigError, match="Unbekannter Provider"):
            get_llm_client()

    def test_empty_api_key_raises_config_error(self, db_path):
        from kern.db import set_config
        from kern.brain import get_llm_client
        from kern.exceptions import ConfigError
        set_config("llm_provider", "anthropic")
        # No api key set
        with pytest.raises(ConfigError, match="Kein API-Key"):
            get_llm_client()

    def test_client_is_cached(self, db_path):
        from kern.db import set_config
        set_config("llm_provider", "anthropic")
        set_config("llm_api_key", "test-key")
        with patch("anthropic.Anthropic") as mock_cls:
            from kern.brain import get_llm_client
            _, client1 = get_llm_client()
            _, client2 = get_llm_client()
            # Should only create the client once
            mock_cls.assert_called_once()

    def test_cache_invalidation(self, db_path):
        from kern.db import set_config
        from kern.brain import get_llm_client, invalidate_client_cache
        set_config("llm_provider", "anthropic")
        set_config("llm_api_key", "test-key")
        with patch("anthropic.Anthropic") as mock_cls:
            get_llm_client()
            invalidate_client_cache()
            get_llm_client()
            assert mock_cls.call_count == 2


class TestChat:
    def test_anthropic_chat(self, db_path, mock_llm):
        from kern.brain import chat
        mock_llm["set_response"]("Hallo Welt")
        result = chat([{"role": "user", "content": "hi"}])
        assert result == "Hallo Welt"

    def test_openai_chat(self, db_path, mock_llm_openai):
        from kern.brain import chat
        mock_llm_openai["set_response"]("Hello World")
        result = chat([{"role": "user", "content": "hi"}])
        assert result == "Hello World"

    def test_empty_response_raises_llm_error(self, db_path, mock_llm):
        from kern.brain import chat
        from kern.exceptions import LLMError
        mock_llm["response"].content = []
        with pytest.raises(LLMError, match="Leere Antwort"):
            chat([{"role": "user", "content": "hi"}])

    def test_openai_empty_response_raises_llm_error(self, db_path, mock_llm_openai):
        from kern.brain import chat
        from kern.exceptions import LLMError
        mock_llm_openai["response"].choices = []
        with pytest.raises(LLMError, match="Leere Antwort"):
            chat([{"role": "user", "content": "hi"}])

    def test_network_error_wraps_in_llm_error(self, db_path, mock_llm):
        from kern.brain import chat
        from kern.exceptions import LLMError
        mock_llm["client"].messages.create.side_effect = ConnectionError("timeout")
        with pytest.raises(LLMError, match="ConnectionError"):
            chat([{"role": "user", "content": "hi"}])

    def test_chat_with_system_prompt(self, db_path, mock_llm):
        from kern.brain import chat
        mock_llm["set_response"]("ok")
        chat([{"role": "user", "content": "hi"}], system="Be brief")
        call_kwargs = mock_llm["client"].messages.create.call_args.kwargs
        assert call_kwargs["system"] == "Be brief"


class TestMemoryChat:
    def test_basic_anthropic(self, db_path, mock_llm):
        from kern.brain import memory_chat
        mock_llm["set_response"]("memory result")
        result = memory_chat("test prompt")
        assert result == "memory result"

    def test_basic_openai(self, db_path, mock_llm_openai):
        from kern.brain import memory_chat
        mock_llm_openai["set_response"]("openai memory")
        result = memory_chat("test prompt")
        assert result == "openai memory"

    def test_with_system_prompt(self, db_path, mock_llm):
        from kern.brain import memory_chat
        mock_llm["set_response"]("ok")
        memory_chat("prompt", system="system instruction")
        call_kwargs = mock_llm["client"].messages.create.call_args.kwargs
        assert call_kwargs["system"] == "system instruction"


class TestChatStream:
    def test_stream_anthropic(self, db_path, mock_llm):
        from kern.brain import chat_stream
        stream_ctx = MagicMock()
        stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
        stream_ctx.__exit__ = MagicMock(return_value=False)
        stream_ctx.text_stream = iter(["Hello", " World"])
        mock_llm["client"].messages.stream.return_value = stream_ctx

        chunks = list(chat_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["Hello", " World"]

    def test_stream_openai(self, db_path, mock_llm_openai):
        from kern.brain import chat_stream
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Hello"
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = " World"
        mock_llm_openai["client"].chat.completions.create.return_value = iter([chunk1, chunk2])

        chunks = list(chat_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["Hello", " World"]
