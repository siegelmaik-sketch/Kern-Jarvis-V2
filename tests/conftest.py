"""
Shared fixtures for Jarvis V2 tests.
All tests use a temporary SQLite DB, no real API calls.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

SCHEMA_PATH = Path(__file__).parent.parent / "kern" / "schema.sql"


@pytest.fixture()
def db_path(tmp_path):
    """Provide a temporary database path and patch kern.db to use it."""
    test_db = tmp_path / "test_jarvis.db"

    with patch("kern.db.DB_PATH", test_db):
        from kern.db import init_db
        init_db()
        yield test_db


@pytest.fixture()
def mock_llm():
    """Mock all LLM calls — Anthropic provider."""
    with patch("kern.brain.get_llm_client") as mock_client:
        provider_client = MagicMock()
        mock_client.return_value = ("anthropic", provider_client)

        response = MagicMock()
        response.content = [MagicMock(text="Mocked LLM response")]
        provider_client.messages.create.return_value = response

        yield {
            "get_llm_client": mock_client,
            "client": provider_client,
            "response": response,
            "set_response": lambda text: setattr(response.content[0], "text", text),
        }


@pytest.fixture()
def mock_llm_openai():
    """Mock all LLM calls — OpenAI provider."""
    with patch("kern.brain.get_llm_client") as mock_client:
        provider_client = MagicMock()
        mock_client.return_value = ("openai", provider_client)

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Mocked OpenAI response"
        provider_client.chat.completions.create.return_value = response

        yield {
            "get_llm_client": mock_client,
            "client": provider_client,
            "response": response,
            "set_response": lambda text: setattr(response.choices[0].message, "content", text),
        }


@pytest.fixture()
def mock_embedding():
    """Mock embedding API calls — returns deterministic 1024-dim unit vectors."""
    import numpy as np

    def fake_embedding(text):
        import hashlib
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
        rng = np.random.RandomState(seed)
        vec = rng.randn(1024).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    with patch("kern.memory._get_embedding", side_effect=fake_embedding) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset global mutable state between tests."""
    yield
    # Reset implicit_memory state
    try:
        import kern.implicit_memory as im
        with im._extraction_lock:
            im._last_extraction = None
    except (ImportError, AttributeError):
        pass
    # Reset memory topic state
    try:
        import kern.memory as mem
        with mem._topic_lock:
            mem._conversation_topic = ""
            mem._topic_keywords = []
            mem._topic_message_count = 0
            mem._topic_updating = False
    except (ImportError, AttributeError):
        pass
    # Reset brain client cache
    try:
        import kern.brain as brain
        with brain._client_lock:
            brain._client_cache.clear()
    except (ImportError, AttributeError):
        pass
