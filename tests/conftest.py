"""
Shared fixtures for Jarvis V2 tests.
All tests use a temporary SQLite DB, no real API calls.
"""
import sqlite3
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
def db_conn(db_path):
    """Provide an initialized DB connection for direct inspection."""
    from kern.db import get_connection
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture()
def mock_llm():
    """Mock all LLM calls — returns configurable responses."""
    mock = MagicMock()
    mock.return_value = "Mocked LLM response"

    with patch("kern.brain.get_llm_client") as mock_client:
        provider_client = MagicMock()
        mock_client.return_value = ("anthropic", provider_client)

        # Default: messages.create returns a proper response
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
def mock_embedding():
    """Mock embedding API calls — returns deterministic 1024-dim unit vectors."""
    import numpy as np

    def fake_embedding(text):
        # Deterministic: seed RNG from text hash for reproducible 1024-dim vectors
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
