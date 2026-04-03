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
        init_db()
        init_db()

    def test_wal_mode_enabled(self, db_path):
        from kern.db import get_connection
        conn = get_connection()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_foreign_keys_enabled(self, db_path):
        from kern.db import get_connection
        conn = get_connection()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        assert fk == 1


class TestConnectionContextManager:
    def test_connection_closes_on_exit(self, db_path):
        from kern.db import connection
        with connection() as conn:
            conn.execute("SELECT 1")
        # Verify connection is closed
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_connection_closes_on_exception(self, db_path):
        from kern.db import connection
        conn_ref = None
        with pytest.raises(ValueError):
            with connection() as conn:
                conn_ref = conn
                raise ValueError("test error")
        # Connection should still be closed
        with pytest.raises(Exception):
            conn_ref.execute("SELECT 1")


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

    def test_many_sequential_reads(self, db_path):
        """Verify connections are properly closed in a loop."""
        from kern.db import get_config, set_config
        set_config("loop_key", "value")
        for _ in range(100):
            assert get_config("loop_key") == "value"


class TestIsConfigured:
    def test_not_configured_by_default(self, db_path):
        from kern.db import is_configured
        assert is_configured() is False

    def test_configured_after_onboarding(self, db_path):
        from kern.db import is_configured, set_config
        set_config("onboarding_done", "true")
        assert is_configured() is True
