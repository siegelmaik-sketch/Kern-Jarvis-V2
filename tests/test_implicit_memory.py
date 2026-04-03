"""Tests for kern.implicit_memory — extraction, filtering, storage."""
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta


class TestExtractFromConversation:
    def test_short_conversation_skipped(self, db_path, mock_embedding):
        from kern.implicit_memory import extract_from_conversation
        result = extract_from_conversation("hi", "hey")
        assert result == []

    def test_system_messages_skipped(self, db_path, mock_embedding):
        from kern.implicit_memory import extract_from_conversation
        result = extract_from_conversation("[SYSTEM] reset", "OK done" * 50)
        assert result == []

    def test_successful_extraction(self, db_path, mock_embedding):
        import kern.implicit_memory as im
        # Reset cooldown
        im._last_extraction = None

        llm_response = json.dumps([
            {"type": "todo", "content": "Deploy am Freitag", "confidence": 0.9, "importance": 8}
        ])

        with patch("kern.brain.memory_chat", return_value=llm_response):
            result = im.extract_from_conversation(
                "Ich muss am Freitag deployen, vergiss das nicht",
                "Alright, ich merke mir das. Deploy ist am Freitag geplant." * 5,
            )
            assert len(result) == 1
            assert result[0]["type"] == "todo"

    def test_low_confidence_filtered(self, db_path, mock_embedding):
        import kern.implicit_memory as im
        im._last_extraction = None

        llm_response = json.dumps([
            {"type": "fakt", "content": "Vielleicht was", "confidence": 0.3, "importance": 3},
            {"type": "todo", "content": "Sicher was", "confidence": 0.9, "importance": 7},
        ])

        with patch("kern.brain.memory_chat", return_value=llm_response):
            result = im.extract_from_conversation(
                "Ich bin mir nicht sicher aber deploy am Freitag" * 3,
                "Ok, ich habe das notiert. " * 10,
            )
            assert len(result) == 1
            assert result[0]["content"] == "Sicher was"

    def test_cooldown_respected(self, db_path, mock_embedding):
        import kern.implicit_memory as im
        im._last_extraction = datetime.now()  # Just extracted

        result = im.extract_from_conversation(
            "Langer Input " * 50,
            "Lange Antwort " * 50,
        )
        assert result == []

    def test_json_parsing_with_code_block(self, db_path, mock_embedding):
        import kern.implicit_memory as im
        im._last_extraction = None

        llm_response = '```json\n[{"type": "entscheidung", "content": "Wir nehmen AWS", "confidence": 0.95, "importance": 9}]\n```'

        with patch("kern.brain.memory_chat", return_value=llm_response):
            result = im.extract_from_conversation(
                "Wir haben entschieden, dass wir AWS nutzen werden" * 3,
                "Gut, ich habe die Entscheidung gespeichert " * 5,
            )
            assert len(result) == 1

    def test_empty_array_response(self, db_path, mock_embedding):
        import kern.implicit_memory as im
        im._last_extraction = None

        with patch("kern.brain.memory_chat", return_value="[]"):
            result = im.extract_from_conversation(
                "Wie geht es dir heute so?" * 10,
                "Mir geht es gut, danke der Nachfrage!" * 10,
            )
            assert result == []

    def test_llm_error_handled(self, db_path, mock_embedding):
        import kern.implicit_memory as im
        im._last_extraction = None

        with patch("kern.brain.memory_chat", side_effect=Exception("API error")):
            result = im.extract_from_conversation(
                "Test message " * 50,
                "Test response " * 50,
            )
            assert result == []


class TestStoreItems:
    def test_stores_with_correct_category(self, db_path, mock_embedding):
        from kern.implicit_memory import _store_items
        from kern.memory import get_facts

        items = [
            {"type": "todo", "content": "Deploy vorbereiten", "importance": 7},
            {"type": "entscheidung", "content": "Nutzen AWS", "importance": 9},
        ]
        _store_items(items)

        facts = get_facts()
        categories = {f["category"] for f in facts}
        assert "todo" in categories
        assert "decision" in categories

    def test_importance_capped(self, db_path, mock_embedding):
        from kern.implicit_memory import _store_items
        from kern.memory import get_facts

        items = [
            {"type": "fakt", "content": "Extrem wichtig", "importance": 10},
        ]
        _store_items(items)

        facts = get_facts()
        assert facts[0]["importance"] <= 8  # capped at 8

    def test_labels_applied(self, db_path, mock_embedding):
        from kern.implicit_memory import _store_items
        from kern.memory import get_facts

        items = [{"type": "zusage", "content": "Anruf morgen", "importance": 6}]
        _store_items(items)

        facts = get_facts()
        assert "[Zusage]" in facts[0]["fact"]
