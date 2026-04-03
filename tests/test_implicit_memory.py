"""Tests for kern.implicit_memory — extraction, filtering, storage."""
import json
import pytest
from unittest.mock import patch
from datetime import datetime


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
        from kern.implicit_memory import extract_from_conversation

        llm_response = json.dumps([
            {"type": "todo", "content": "Deploy am Freitag", "confidence": 0.9, "importance": 8}
        ])

        with patch("kern.brain.memory_chat", return_value=llm_response):
            result = extract_from_conversation(
                "Ich muss am Freitag deployen, vergiss das nicht",
                "Alright, ich merke mir das. Deploy ist am Freitag geplant." * 5,
            )
            assert len(result) == 1
            assert result[0]["type"] == "todo"

    def test_low_confidence_filtered(self, db_path, mock_embedding):
        from kern.implicit_memory import extract_from_conversation

        llm_response = json.dumps([
            {"type": "fakt", "content": "Vielleicht was", "confidence": 0.3, "importance": 3},
            {"type": "todo", "content": "Sicher was", "confidence": 0.9, "importance": 7},
        ])

        with patch("kern.brain.memory_chat", return_value=llm_response):
            result = extract_from_conversation(
                "Ich bin mir nicht sicher aber deploy am Freitag" * 3,
                "Ok, ich habe das notiert. " * 10,
            )
            assert len(result) == 1
            assert result[0]["content"] == "Sicher was"

    def test_cooldown_respected(self, db_path, mock_embedding):
        import kern.implicit_memory as im
        with im._extraction_lock:
            im._last_extraction = datetime.now()

        result = im.extract_from_conversation(
            "Langer Input " * 50,
            "Lange Antwort " * 50,
        )
        assert result == []

    def test_cooldown_only_set_after_success(self, db_path, mock_embedding):
        """Cooldown should only be set when items are actually extracted."""
        import kern.implicit_memory as im

        with patch("kern.brain.memory_chat", return_value="[]"):
            im.extract_from_conversation(
                "Wie geht es dir heute so?" * 10,
                "Mir geht es gut, danke der Nachfrage!" * 10,
            )
            # No items extracted, so cooldown should NOT be set
            with im._extraction_lock:
                assert im._last_extraction is None

    def test_json_parsing_with_code_block(self, db_path, mock_embedding):
        from kern.implicit_memory import extract_from_conversation

        llm_response = '```json\n[{"type": "entscheidung", "content": "Wir nehmen AWS", "confidence": 0.95, "importance": 9}]\n```'

        with patch("kern.brain.memory_chat", return_value=llm_response):
            result = extract_from_conversation(
                "Wir haben entschieden, dass wir AWS nutzen werden" * 3,
                "Gut, ich habe die Entscheidung gespeichert " * 5,
            )
            assert len(result) == 1

    def test_empty_array_response(self, db_path, mock_embedding):
        from kern.implicit_memory import extract_from_conversation

        with patch("kern.brain.memory_chat", return_value="[]"):
            result = extract_from_conversation(
                "Wie geht es dir heute so?" * 10,
                "Mir geht es gut, danke der Nachfrage!" * 10,
            )
            assert result == []

    def test_llm_error_handled_gracefully(self, db_path, mock_embedding):
        from kern.implicit_memory import extract_from_conversation

        with patch("kern.brain.memory_chat", side_effect=Exception("API error")):
            result = extract_from_conversation(
                "Test message " * 50,
                "Test response " * 50,
            )
            assert result == []

    def test_dict_response_with_items_key(self, db_path, mock_embedding):
        from kern.implicit_memory import extract_from_conversation

        llm_response = json.dumps({
            "items": [
                {"type": "todo", "content": "Aufgabe X", "confidence": 0.9, "importance": 7}
            ]
        })

        with patch("kern.brain.memory_chat", return_value=llm_response):
            result = extract_from_conversation(
                "Ich muss Aufgabe X erledigen " * 5,
                "Verstanden, ich merke mir Aufgabe X " * 5,
            )
            assert len(result) == 1
            assert result[0]["content"] == "Aufgabe X"


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

    def test_importance_capped_at_8(self, db_path, mock_embedding):
        from kern.implicit_memory import _store_items
        from kern.memory import get_facts

        items = [
            {"type": "fakt", "content": "Extrem wichtig", "importance": 10},
        ]
        _store_items(items)

        facts = get_facts()
        assert facts[0]["importance"] <= 8

    def test_labels_applied(self, db_path, mock_embedding):
        from kern.implicit_memory import _store_items
        from kern.memory import get_facts

        items = [{"type": "zusage", "content": "Anruf morgen", "importance": 6}]
        _store_items(items)

        facts = get_facts()
        assert "[Zusage]" in facts[0]["fact"]

    def test_unknown_type_defaults_to_operational(self, db_path, mock_embedding):
        from kern.implicit_memory import _store_items
        from kern.memory import get_facts

        items = [{"type": "unknown_type", "content": "Something", "importance": 5}]
        _store_items(items)

        facts = get_facts()
        assert facts[0]["category"] == "operational"
