"""Tests for kern.memory — messages, facts, search, context building."""
import json
import numpy as np
import pytest
from unittest.mock import patch, MagicMock


class TestParseLlmJson:
    """Test the parse_llm_json helper — critical for parsing LLM responses."""

    def test_plain_json(self):
        from kern.memory import parse_llm_json
        assert parse_llm_json('{"save": true, "importance": 7}') == {"save": True, "importance": 7}

    def test_json_code_block(self):
        from kern.memory import parse_llm_json
        result = parse_llm_json('```json\n{"save": false}\n```')
        assert result == {"save": False}

    def test_code_block_no_lang(self):
        from kern.memory import parse_llm_json
        result = parse_llm_json('```\n{"save": true}\n```')
        assert result == {"save": True}

    def test_json_with_surrounding_text(self):
        from kern.memory import parse_llm_json
        result = parse_llm_json('Sure, here is the result: {"save": true, "importance": 5}')
        assert result["save"] is True

    def test_invalid_json_returns_none(self):
        from kern.memory import parse_llm_json
        assert parse_llm_json("not json at all") is None

    def test_empty_string_returns_none(self):
        from kern.memory import parse_llm_json
        assert parse_llm_json("") is None

    def test_nested_json_object(self):
        from kern.memory import parse_llm_json
        result = parse_llm_json('{"a": {"b": 1}, "c": 2}')
        assert result == {"a": {"b": 1}, "c": 2}

    def test_nested_json_in_text(self):
        from kern.memory import parse_llm_json
        result = parse_llm_json('Here: {"outer": {"inner": true}, "value": 42}')
        assert result == {"outer": {"inner": True}, "value": 42}

    def test_json_array(self):
        from kern.memory import parse_llm_json
        result = parse_llm_json('[{"type": "todo"}, {"type": "fakt"}]')
        assert isinstance(result, list)
        assert len(result) == 2

    def test_json_array_in_text(self):
        from kern.memory import parse_llm_json
        result = parse_llm_json('Result: [{"save": true}]')
        assert isinstance(result, list)
        assert result[0]["save"] is True

    def test_backward_compat_alias(self):
        from kern.memory import _parse_llm_json, parse_llm_json
        assert _parse_llm_json is parse_llm_json


class TestCosineSimilarity:
    def test_identical_vectors(self):
        from kern.memory import _cosine_similarity
        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        from kern.memory import _cosine_similarity
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        from kern.memory import _cosine_similarity
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-6

    def test_zero_vector(self):
        from kern.memory import _cosine_similarity
        a = np.array([1.0, 2.0], dtype=np.float32)
        b = np.array([0.0, 0.0], dtype=np.float32)
        assert _cosine_similarity(a, b) == 0.0

    def test_dimension_mismatch_returns_zero(self):
        from kern.memory import _cosine_similarity
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = np.array([1.0, 2.0], dtype=np.float32)
        assert _cosine_similarity(a, b) == 0.0

    def test_nan_handling(self):
        from kern.memory import _cosine_similarity
        a = np.array([float('inf'), 1.0], dtype=np.float32)
        b = np.array([1.0, 1.0], dtype=np.float32)
        result = _cosine_similarity(a, b)
        assert result == 0.0 or np.isfinite(result)


class TestEmbeddingConversion:
    def test_roundtrip(self):
        from kern.memory import _embedding_to_blob, _blob_to_embedding
        original = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        blob = _embedding_to_blob(original)
        restored = _blob_to_embedding(blob)
        np.testing.assert_array_equal(original, restored)


class TestAppendMessage:
    def test_basic_append(self, db_path):
        from kern.memory import append_message, get_message_count
        append_message({"role": "user", "content": "Hello"})
        assert get_message_count() == 1

    def test_multiple_messages(self, db_path):
        from kern.memory import append_message, get_message_count
        append_message({"role": "user", "content": "msg1"})
        append_message({"role": "assistant", "content": "msg2"})
        append_message({"role": "user", "content": "msg3"})
        assert get_message_count() == 3

    def test_with_tool_calls(self, db_path):
        from kern.memory import append_message, load_context
        append_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "123", "function": {"name": "test"}}]
        })
        ctx = load_context()
        assert len(ctx) == 1
        assert ctx[0]["tool_calls"] == [{"id": "123", "function": {"name": "test"}}]


class TestLoadContext:
    def test_empty_db(self, db_path):
        from kern.memory import load_context
        assert load_context() == []

    def test_returns_chronological_order(self, db_path):
        from kern.memory import append_message, load_context
        append_message({"role": "user", "content": "first"})
        append_message({"role": "assistant", "content": "second"})
        append_message({"role": "user", "content": "third"})
        ctx = load_context()
        assert ctx[0]["content"] == "first"
        assert ctx[-1]["content"] == "third"

    def test_respects_limit(self, db_path):
        from kern.memory import append_message, load_context
        for i in range(30):
            append_message({"role": "user", "content": f"msg {i}"})
        ctx = load_context(max_messages=5)
        assert len(ctx) == 5
        assert ctx[-1]["content"] == "msg 29"
        assert ctx[0]["content"] == "msg 25"


class TestClearMessages:
    def test_clears_all(self, db_path):
        from kern.memory import append_message, clear_messages, get_message_count
        append_message({"role": "user", "content": "msg"})
        assert get_message_count() == 1
        clear_messages()
        assert get_message_count() == 0


class TestSaveFact:
    def test_basic_save(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_facts
        result = save_fact("User heißt Maik", category="preference", source="user")
        assert result is True
        facts = get_facts()
        assert len(facts) == 1
        assert facts[0]["fact"] == "User heißt Maik"

    def test_duplicate_ignored(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_facts
        save_fact("Fakt 1", source="user")
        save_fact("Fakt 1", source="user")
        assert len(get_facts()) == 1

    def test_user_gets_high_importance(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_facts
        save_fact("Fakt", source="user")
        facts = get_facts()
        assert facts[0]["importance"] == 7

    def test_agent_fact_goes_through_gate(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_facts
        with patch("kern.memory._gate_fact", return_value=(True, 6)) as gate:
            save_fact("Fakt", source="agent")
            gate.assert_called_once()
            facts = get_facts()
            assert len(facts) == 1
            assert facts[0]["importance"] == 6

    def test_agent_fact_rejected_by_gate(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_facts
        with patch("kern.memory._gate_fact", return_value=(False, 3)):
            result = save_fact("Trivial fakt", source="agent")
            assert result is False
            assert len(get_facts()) == 0

    def test_embedding_none_still_saves(self, db_path):
        from kern.memory import save_fact, get_facts
        with patch("kern.memory._get_embedding", return_value=None):
            save_fact("Fakt ohne Embedding", source="user")
            facts = get_facts()
            assert len(facts) == 1


class TestDeleteFact:
    def test_delete_existing(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_facts, delete_fact
        save_fact("To delete", source="user")
        facts = get_facts()
        assert delete_fact(facts[0]["id"]) is True
        assert len(get_facts()) == 0

    def test_delete_nonexistent(self, db_path):
        from kern.memory import delete_fact
        assert delete_fact(9999) is False


class TestGetRelevantFacts:
    def test_returns_facts(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_relevant_facts
        save_fact("Bitcoin steht bei 50k", category="operational", source="user", importance=8)
        save_fact("User mag Pizza", category="preference", source="user", importance=9)
        facts = get_relevant_facts(query="Bitcoin")
        assert len(facts) >= 1

    def test_always_load_preferences(self, db_path, mock_embedding):
        from kern.memory import save_fact, get_relevant_facts
        save_fact("User Name: Maik", category="preference", source="user", importance=9)
        facts = get_relevant_facts(query="unrelated topic")
        pref = [f for f in facts if f["category"] == "preference"]
        assert len(pref) >= 1

    def test_empty_db(self, db_path, mock_embedding):
        from kern.memory import get_relevant_facts
        assert get_relevant_facts(query="test") == []


class TestSearchFacts:
    def test_with_embedding(self, db_path, mock_embedding):
        from kern.memory import save_fact, search_facts
        save_fact("Bitcoin Kurs aktuell", source="user")
        save_fact("Wetter in Berlin", source="user")
        results = search_facts("Bitcoin")
        assert len(results) >= 1
        assert "similarity" in results[0]

    def test_without_embedding_fallback(self, db_path, mock_embedding):
        from kern.memory import save_fact, search_facts
        save_fact("Test fact", source="user")
        with patch("kern.memory._get_embedding", return_value=None):
            results = search_facts("test")
            assert len(results) >= 1


class TestMemorySave:
    def test_saves_with_category(self, db_path, mock_embedding):
        from kern.memory import memory_save, get_facts
        memory_save("user", "name", "Maik")
        facts = get_facts()
        assert len(facts) == 1
        assert "[name]" in facts[0]["fact"]
        assert "Maik" in facts[0]["fact"]
        assert facts[0]["category"] == "preference"


class TestSearchFactByKey:
    def test_finds_by_prefix(self, db_path, mock_embedding):
        from kern.memory import memory_save, search_fact_by_key
        memory_save("user", "lieblings_essen", "Pizza")
        results = search_fact_by_key("lieblings_essen")
        assert len(results) == 1

    def test_no_match(self, db_path):
        from kern.memory import search_fact_by_key
        assert search_fact_by_key("nonexistent") == []


class TestArchiveConversation:
    def test_basic_archive(self, db_path, mock_embedding):
        from kern.memory import archive_conversation
        aid = archive_conversation(
            topic="Test Topic",
            summary="Test summary",
            keywords=["test", "archive"],
        )
        assert aid is not None
        assert isinstance(aid, int)

    def test_archive_with_messages(self, db_path, mock_embedding):
        from kern.memory import archive_conversation
        msgs = [{"role": "user", "content": "hi"}]
        aid = archive_conversation(
            topic="Chat",
            summary="Short chat",
            keywords=["chat"],
            messages=msgs,
        )
        assert aid is not None


class TestSearchArchives:
    def test_finds_archive(self, db_path, mock_embedding):
        from kern.memory import archive_conversation, search_archives
        archive_conversation(
            topic="Bitcoin Analysis",
            summary="Discussion about BTC price",
            keywords=["bitcoin", "crypto"],
        )
        results = search_archives("Bitcoin")
        assert len(results) >= 1
        assert "similarity" in results[0]


class TestBuildMemoryContext:
    def test_empty_db(self, db_path, mock_embedding):
        from kern.memory import build_memory_context
        assert build_memory_context() == ""

    def test_with_facts(self, db_path, mock_embedding):
        from kern.memory import save_fact, build_memory_context
        save_fact("User heißt Maik", category="preference", source="user", importance=9)
        ctx = build_memory_context(query="Name")
        assert "Maik" in ctx
        assert "Memory" in ctx


class TestGateFact:
    def test_gate_approval(self, db_path):
        from kern.memory import _gate_fact
        with patch("kern.brain.memory_chat", return_value='{"save": true, "importance": 8}'):
            should_save, importance = _gate_fact("User phone: 0171-123456", "preference")
            assert should_save is True
            assert importance == 8

    def test_gate_rejection(self, db_path):
        from kern.memory import _gate_fact
        with patch("kern.brain.memory_chat", return_value='{"save": false, "importance": 2}'):
            should_save, importance = _gate_fact("General knowledge", "general")
            assert should_save is False

    def test_gate_failure_allows_fact(self, db_path):
        from kern.memory import _gate_fact
        with patch("kern.brain.memory_chat", side_effect=Exception("API down")):
            should_save, importance = _gate_fact("Some fact", "general")
            assert should_save is True
            assert importance == 5


class TestConversationTopic:
    def test_initial_topic_empty(self):
        from kern.memory import get_conversation_topic
        assert get_conversation_topic() == ""

    def test_update_topic(self, db_path):
        from kern.memory import update_conversation_topic, get_conversation_topic
        import kern.memory as mem
        # Force topic update by setting counter to threshold - 1
        with mem._topic_lock:
            mem._topic_message_count = 4

        messages = [
            {"role": "user", "content": "Was kostet Bitcoin?"},
            {"role": "assistant", "content": "Bitcoin steht bei 50k."},
        ]
        with patch("kern.brain.memory_chat", return_value="TOPIC: Bitcoin Kurs\nKEYWORDS: bitcoin, kurs, preis"):
            update_conversation_topic(messages)
            assert "Bitcoin" in get_conversation_topic()
