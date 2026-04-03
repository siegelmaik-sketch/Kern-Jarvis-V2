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
        from kern.brain import get_kern_prompt, KERN_PROMPT_PATH
        with patch("kern.brain.KERN_PROMPT_PATH", tmp_path / "nonexistent.md"):
            from kern.brain import get_kern_prompt
            # Re-import won't work, call with patched path
            import kern.brain
            old = kern.brain.KERN_PROMPT_PATH
            kern.brain.KERN_PROMPT_PATH = tmp_path / "nonexistent.md"
            try:
                result = kern.brain.get_kern_prompt()
                assert "Jarvis" in result  # fallback prompt
            finally:
                kern.brain.KERN_PROMPT_PATH = old


class TestBuildSystemPrompt:
    def test_basic(self):
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt()
            assert result == "KERN"

    def test_with_memory(self):
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt(memory_context="MEMORY")
            assert "KERN" in result
            assert "MEMORY" in result

    def test_with_tools(self):
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt(tools_manifest="TOOLS")
            assert "TOOLS" in result

    def test_with_both(self):
        from kern.brain import build_system_prompt
        with patch("kern.brain.get_kern_prompt", return_value="KERN"):
            result = build_system_prompt(memory_context="MEM", tools_manifest="TOOLS")
            assert "MEM" in result
            assert "TOOLS" in result


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


class TestGetLlmClient:
    def test_anthropic(self, db_path):
        from kern.db import set_config
        set_config("llm_provider", "anthropic")
        set_config("llm_api_key", "test-key")
        with patch("anthropic.Anthropic") as mock_cls:
            from kern.brain import get_llm_client
            provider, client = get_llm_client()
            assert provider == "anthropic"
            mock_cls.assert_called_once_with(api_key="test-key")

    def test_openai(self, db_path):
        from kern.db import set_config
        set_config("llm_provider", "openai")
        set_config("llm_api_key", "test-key")
        with patch("openai.OpenAI") as mock_cls:
            from kern.brain import get_llm_client
            provider, client = get_llm_client()
            assert provider == "openai"

    def test_unknown_provider_raises(self, db_path):
        from kern.db import set_config
        from kern.brain import get_llm_client
        set_config("llm_provider", "unknown_provider")
        with pytest.raises(ValueError, match="Unbekannter Provider"):
            get_llm_client()


class TestChat:
    def test_anthropic_chat(self, db_path, mock_llm):
        from kern.brain import chat
        mock_llm["set_response"]("Hallo Welt")
        result = chat([{"role": "user", "content": "hi"}])
        assert result == "Hallo Welt"

    def test_empty_response_raises(self, db_path, mock_llm):
        from kern.brain import chat, LLMError
        mock_llm["response"].content = []
        with pytest.raises(LLMError, match="Leere Antwort"):
            chat([{"role": "user", "content": "hi"}])

    def test_network_error_wraps(self, db_path, mock_llm):
        from kern.brain import chat, LLMError
        mock_llm["client"].messages.create.side_effect = ConnectionError("timeout")
        with pytest.raises(LLMError, match="ConnectionError"):
            chat([{"role": "user", "content": "hi"}])


class TestMemoryChat:
    def test_basic(self, db_path, mock_llm):
        from kern.brain import memory_chat
        mock_llm["set_response"]("memory result")
        result = memory_chat("test prompt")
        assert result == "memory result"

    def test_with_system(self, db_path, mock_llm):
        from kern.brain import memory_chat
        mock_llm["set_response"]("ok")
        result = memory_chat("prompt", system="system instruction")
        assert result == "ok"
        call_kwargs = mock_llm["client"].messages.create.call_args.kwargs
        assert call_kwargs["system"] == "system instruction"


class TestChatStream:
    def test_stream_anthropic(self, db_path, mock_llm):
        from kern.brain import chat_stream

        # Mock the stream context manager
        stream_ctx = MagicMock()
        stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
        stream_ctx.__exit__ = MagicMock(return_value=False)
        stream_ctx.text_stream = iter(["Hello", " World"])
        mock_llm["client"].messages.stream.return_value = stream_ctx

        chunks = list(chat_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["Hello", " World"]
