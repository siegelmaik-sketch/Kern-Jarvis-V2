"""Tests for kern.web — SearXNG search + URL fetch with mocked httpx."""
import time

import pytest
from unittest.mock import patch, MagicMock

import httpx


@pytest.fixture(autouse=True)
def _disable_cache_by_default(db_path):
    """
    Disable web_cache by default for the search tests in this file. The
    cache is opt-in tested separately in TestWebCache.
    Depends on db_path so the config table exists.
    """
    from kern.db import set_config
    set_config("web_cache_ttl", "0")


# ── web_search ────────────────────────────────────────────────────────────────


class TestWebSearch:
    def test_web_search_with_valid_query_returns_results(self, db_path):
        # Arrange
        from kern.web import web_search
        fake_payload = {
            "results": [
                {
                    "title": "Sosa (Eibenstock)",
                    "url": "https://de.wikipedia.org/wiki/Sosa",
                    "content": "Ortsteil der Stadt Eibenstock im Erzgebirgskreis.",
                    "engine": "wikipedia",
                },
                {
                    "title": "Sosa Talsperre",
                    "url": "https://example.com/sosa",
                    "content": "Trinkwassertalsperre.",
                    "engine": "duckduckgo",
                },
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = fake_payload
        mock_response.raise_for_status.return_value = None

        # Act
        with patch("kern.web.httpx.get", return_value=mock_response):
            results = web_search("Sosa Eibenstock")

        # Assert
        assert len(results) == 2
        assert results[0]["title"] == "Sosa (Eibenstock)"
        assert results[0]["url"] == "https://de.wikipedia.org/wiki/Sosa"
        assert results[0]["snippet"].startswith("Ortsteil")
        assert results[0]["engine"] == "wikipedia"

    def test_web_search_respects_max_results(self, db_path):
        # Arrange
        from kern.web import web_search
        fake_payload = {
            "results": [
                {"title": f"r{i}", "url": f"http://x/{i}", "content": "", "engine": "x"}
                for i in range(10)
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = fake_payload
        mock_response.raise_for_status.return_value = None

        # Act
        with patch("kern.web.httpx.get", return_value=mock_response):
            results = web_search("foo", max_results=3)

        # Assert
        assert len(results) == 3

    def test_web_search_passes_language_from_config(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("search_language", "en")

        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status.return_value = None

        # Act
        with patch("kern.web.httpx.get", return_value=mock_response) as mock_get:
            web_search("test query")

        # Assert
        params = mock_get.call_args.kwargs["params"]
        assert params["language"] == "en"
        assert params["q"] == "test query"
        assert params["format"] == "json"

    def test_web_search_uses_configured_searxng_url(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("searxng_url", "http://my-searxng:9999")

        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status.return_value = None

        # Act
        with patch("kern.web.httpx.get", return_value=mock_response) as mock_get:
            web_search("test")

        # Assert
        called_url = mock_get.call_args.args[0]
        assert called_url == "http://my-searxng:9999/search"

    def test_web_search_with_empty_query_raises_value_error(self, db_path):
        from kern.web import web_search
        with pytest.raises(ValueError, match="non-empty"):
            web_search("")

    def test_web_search_with_whitespace_only_raises_value_error(self, db_path):
        from kern.web import web_search
        with pytest.raises(ValueError, match="non-empty"):
            web_search("   \n\t")

    def test_web_search_with_zero_max_results_raises_value_error(self, db_path):
        from kern.web import web_search
        with pytest.raises(ValueError, match="max_results"):
            web_search("foo", max_results=0)

    def test_web_search_on_http_error_raises_websearch_api_error(self, db_path):
        # Arrange
        from kern.web import web_search
        from kern.exceptions import WebSearchAPIError

        # Act + Assert
        with patch("kern.web.httpx.get", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(WebSearchAPIError, match="SearXNG request failed"):
                web_search("foo")

    def test_web_search_on_5xx_raises_websearch_api_error(self, db_path):
        # Arrange
        from kern.web import web_search
        from kern.exceptions import WebSearchAPIError

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        # Act + Assert
        with patch("kern.web.httpx.get", return_value=mock_response):
            with pytest.raises(WebSearchAPIError):
                web_search("foo")

    def test_web_search_on_invalid_json_raises_websearch_api_error(self, db_path):
        # Arrange
        from kern.web import web_search
        from kern.exceptions import WebSearchAPIError

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError("not json")

        # Act + Assert
        with patch("kern.web.httpx.get", return_value=mock_response):
            with pytest.raises(WebSearchAPIError, match="non-JSON"):
                web_search("foo")

    def test_web_search_on_missing_results_key_raises_websearch_api_error(self, db_path):
        # Arrange
        from kern.web import web_search
        from kern.exceptions import WebSearchAPIError

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"results": "not a list"}

        # Act + Assert
        with patch("kern.web.httpx.get", return_value=mock_response):
            with pytest.raises(WebSearchAPIError, match="results"):
                web_search("foo")

    def test_web_search_strips_whitespace_in_results(self, db_path):
        # Arrange
        from kern.web import web_search
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {"title": "  spaced  ", "url": "  http://x  ", "content": "  snip  ", "engine": "e"}
            ]
        }

        # Act
        with patch("kern.web.httpx.get", return_value=mock_response):
            results = web_search("foo")

        # Assert
        assert results[0]["title"] == "spaced"
        assert results[0]["url"] == "http://x"
        assert results[0]["snippet"] == "snip"


# ── web_fetch ─────────────────────────────────────────────────────────────────


class TestWebFetch:
    def _mock_httpx_client(self, status: int = 200, content: bytes = b"", encoding: str = "utf-8"):
        """Helper to build a mocked httpx.Client context manager."""
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.content = content
        mock_response.encoding = encoding
        mock_response.url = "https://example.com/page"
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.get.return_value = mock_response
        return mock_client, mock_response

    def test_web_fetch_extracts_main_text(self, db_path):
        # Arrange
        from kern.web import web_fetch
        html = (
            b"<html><head><title>Test Page</title></head>"
            b"<body><nav>menu</nav><article>"
            b"<h1>Hauptueberschrift</h1>"
            b"<p>Das ist der Hauptinhalt eines Artikels mit ausreichend Text "
            b"um die Boilerplate-Erkennung von trafilatura zu ueberzeugen. "
            b"Hier kommt noch mehr Inhalt damit die Heuristik anschlaegt. "
            b"Lorem ipsum dolor sit amet consectetur adipiscing elit.</p>"
            b"</article><footer>footer text</footer></body></html>"
        )
        mock_client, _ = self._mock_httpx_client(content=html)

        # Act
        with patch("kern.web.httpx.Client", return_value=mock_client):
            result = web_fetch("https://example.com/page")

        # Assert
        assert "Hauptinhalt" in result["text"]
        assert result["truncated"] is False
        assert result["url"] == "https://example.com/page"

    def test_web_fetch_with_empty_url_raises_value_error(self, db_path):
        from kern.web import web_fetch
        with pytest.raises(ValueError, match="non-empty"):
            web_fetch("")

    def test_web_fetch_with_too_small_max_chars_raises_value_error(self, db_path):
        from kern.web import web_fetch
        with pytest.raises(ValueError, match="max_chars"):
            web_fetch("https://example.com", max_chars=10)

    def test_web_fetch_truncates_when_text_exceeds_max_chars(self, db_path):
        # Arrange
        from kern.web import web_fetch
        long_text = "Wort " * 5000
        html = f"<html><body><article><p>{long_text}</p></article></body></html>".encode()
        mock_client, _ = self._mock_httpx_client(content=html)

        # Act
        with patch("kern.web.httpx.Client", return_value=mock_client):
            result = web_fetch("https://example.com", max_chars=500)

        # Assert
        assert len(result["text"]) == 500
        assert result["truncated"] is True

    def test_web_fetch_on_http_error_raises_webfetch_error(self, db_path):
        # Arrange
        from kern.web import web_fetch
        from kern.exceptions import WebFetchError

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.get.side_effect = httpx.ConnectError("nope")

        # Act + Assert
        with patch("kern.web.httpx.Client", return_value=mock_client):
            with pytest.raises(WebFetchError, match="Fetch failed"):
                web_fetch("https://broken.example")

    def test_web_fetch_on_unextractable_content_raises_webfetch_error(self, db_path):
        # Arrange
        from kern.web import web_fetch
        from kern.exceptions import WebFetchError

        # Trafilatura returns nothing for empty body
        html = b"<html><body></body></html>"
        mock_client, _ = self._mock_httpx_client(content=html)

        # Act + Assert
        with patch("kern.web.httpx.Client", return_value=mock_client):
            with pytest.raises(WebFetchError, match="extract"):
                web_fetch("https://empty.example")


# ── web_cache ─────────────────────────────────────────────────────────────────


class TestWebCache:
    def _mock_search_response(self, results: list[dict]) -> MagicMock:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"results": results}
        return mock_response

    def test_second_call_hits_cache(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("web_cache_ttl", "3600")

        fake = self._mock_search_response([
            {"title": "T", "url": "http://x", "content": "c", "engine": "e"}
        ])

        # Act
        with patch("kern.web.httpx.get", return_value=fake) as mock_get:
            r1 = web_search("foo")
            r2 = web_search("foo")

        # Assert
        assert mock_get.call_count == 1  # second call served from cache
        assert r1 == r2

    def test_cache_respects_max_results_after_lookup(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("web_cache_ttl", "3600")

        fake = self._mock_search_response([
            {"title": f"t{i}", "url": f"http://x/{i}", "content": "", "engine": "e"}
            for i in range(10)
        ])

        # Act
        with patch("kern.web.httpx.get", return_value=fake) as mock_get:
            web_search("foo", max_results=10)        # primes cache with 10
            r2 = web_search("foo", max_results=3)    # cache hit, slice to 3

        # Assert
        assert mock_get.call_count == 1
        assert len(r2) == 3

    def test_cache_separated_by_language(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("web_cache_ttl", "3600")

        fake = self._mock_search_response([
            {"title": "T", "url": "http://x", "content": "c", "engine": "e"}
        ])

        # Act
        with patch("kern.web.httpx.get", return_value=fake) as mock_get:
            set_config("search_language", "de")
            web_search("foo")
            set_config("search_language", "en")
            web_search("foo")

        # Assert — different languages → two real requests
        assert mock_get.call_count == 2

    def test_cache_expires_after_ttl(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("web_cache_ttl", "3600")

        fake = self._mock_search_response([
            {"title": "T", "url": "http://x", "content": "c", "engine": "e"}
        ])

        # Act — first call with current time, second after ttl expired
        with patch("kern.web.httpx.get", return_value=fake) as mock_get:
            web_search("foo")
            # Force-expire the cache by rewriting created_at to the distant past
            from kern.db import connection
            with connection() as conn:
                conn.execute("UPDATE web_cache SET created_at = 0")
                conn.commit()
            web_search("foo")

        # Assert — both calls hit network
        assert mock_get.call_count == 2

    def test_ttl_zero_disables_cache(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("web_cache_ttl", "0")

        fake = self._mock_search_response([
            {"title": "T", "url": "http://x", "content": "c", "engine": "e"}
        ])

        # Act
        with patch("kern.web.httpx.get", return_value=fake) as mock_get:
            web_search("foo")
            web_search("foo")

        # Assert — every call hits network
        assert mock_get.call_count == 2

    def test_cache_normalizes_whitespace_in_query(self, db_path):
        # Arrange
        from kern.db import set_config
        from kern.web import web_search
        set_config("web_cache_ttl", "3600")

        fake = self._mock_search_response([
            {"title": "T", "url": "http://x", "content": "c", "engine": "e"}
        ])

        # Act
        with patch("kern.web.httpx.get", return_value=fake) as mock_get:
            web_search("  foo bar  ")
            web_search("foo bar")

        # Assert — both queries normalize to "foo bar", second is cache hit
        assert mock_get.call_count == 1


# ── builtin tool dispatch ─────────────────────────────────────────────────────


class TestBuiltinDispatch:
    def test_run_tool_dispatches_web_search_to_builtin(self, db_path):
        # Arrange
        from kern.tools import run_tool
        fake_results = [{"title": "x", "url": "http://x", "snippet": "", "engine": "e"}]

        # Act
        with patch("kern.web.web_search", return_value=fake_results) as mock_search:
            result = run_tool("web_search", {"query": "test", "max_results": 3})

        # Assert
        mock_search.assert_called_once_with("test", max_results=3)
        assert result["success"] is True
        assert result["result"] == fake_results

    def test_run_tool_web_search_missing_query_returns_error(self, db_path):
        # Arrange
        from kern.tools import run_tool

        # Act
        result = run_tool("web_search", {})

        # Assert
        assert result["success"] is False
        assert "query" in result["error"].lower()

    def test_run_tool_web_search_propagates_api_error(self, db_path):
        # Arrange
        from kern.tools import run_tool
        from kern.exceptions import WebSearchAPIError

        # Act
        with patch("kern.web.web_search", side_effect=WebSearchAPIError("dead")):
            result = run_tool("web_search", {"query": "x"})

        # Assert
        assert result["success"] is False
        assert "dead" in result["error"]

    def test_build_tools_manifest_includes_builtin_section(self, db_path):
        # Arrange
        from kern.tools import build_tools_manifest

        # Act
        manifest = build_tools_manifest()

        # Assert
        assert "Builtin Tools" in manifest
        assert "web_search" in manifest
        assert "web_fetch" in manifest
