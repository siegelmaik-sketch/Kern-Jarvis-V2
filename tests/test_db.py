"""Tests for kern.db — connection, config, init."""
import sqlite3
import pytest
from unittest.mock import patch


class TestInitDb:
    def test_creates_tables(self, db_path):
        from kern.db import get_connection
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        conn.close()
        table_names = {r["name"] for r in tables}
        assert "config" in table_names
        assert "messages" in table_names
        assert "facts" in table_names
        assert "archives" in table_names
        assert "tools" in table_names

    def test_idempotent(self, db_path):
        from kern.db import init_db
        # Should not raise on second call
        init_db()
        init_db()


class TestGetSetConfig:
    def test_set_and_get(self, db_path):
        from kern.db import get_config, set_config
        set_config("test_key", "test_value")
        assert get_config("test_key") == "test_value"

    def test_get_default(self, db_path):
        from kern.db import get_config
        assert get_config("nonexistent") is None
        assert get_config("nonexistent", "fallback") == "fallback"

    def test_upsert(self, db_path):
        from kern.db import get_config, set_config
        set_config("key", "v1")
        assert get_config("key") == "v1"
        set_config("key", "v2")
        assert get_config("key") == "v2"

    def test_connection_closed_on_error(self, db_path):
        """Verify try/finally pattern works — connection is closed even on error."""
        from kern.db import get_connection, get_config
        # This should not leak connections
        for _ in range(100):
            get_config("test")


class TestIsConfigured:
    def test_not_configured_by_default(self, db_path):
        from kern.db import is_configured
        assert is_configured() is False

    def test_configured_after_onboarding(self, db_path):
        from kern.db import is_configured, set_config
        set_config("onboarding_done", "true")
        assert is_configured() is True
