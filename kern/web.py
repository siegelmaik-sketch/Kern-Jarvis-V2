"""
Kern-Jarvis V2 — Web Search & Fetch
═══════════════════════════════════
Web search via self-hosted SearXNG (central container in jarvis-shared network).
URL fetch via httpx + trafilatura for boilerplate-free content extraction.

Both functions are exposed as builtin tools through kern/tools.py.
"""
import logging

import httpx

from kern.db import get_config
from kern.exceptions import WebFetchError, WebSearchAPIError

log = logging.getLogger(__name__)

DEFAULT_SEARXNG_URL = "http://searxng:8080"
DEFAULT_LANGUAGE = "de"
DEFAULT_MAX_RESULTS = 5
DEFAULT_TIMEOUT = 15.0
DEFAULT_FETCH_TIMEOUT = 20.0
MAX_FETCH_BYTES = 2_000_000  # 2 MB hard cap


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[dict]:
    """
    Search the web via the central SearXNG instance.

    Returns a list of dicts: {"title": str, "url": str, "snippet": str, "engine": str}.
    Raises WebSearchAPIError on transport failures or non-2xx responses.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if max_results < 1:
        raise ValueError("max_results must be >= 1")

    base = get_config("searxng_url", DEFAULT_SEARXNG_URL)
    language = get_config("search_language", DEFAULT_LANGUAGE)

    try:
        response = httpx.get(
            f"{base}/search",
            params={
                "q": query.strip(),
                "format": "json",
                "language": language,
                "safesearch": "0",
            },
            headers={"User-Agent": "kern-jarvis/2.0"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        log.exception("SearXNG request failed for query=%r", query)
        raise WebSearchAPIError(f"SearXNG request failed: {e}") from e

    try:
        payload = response.json()
    except ValueError as e:
        raise WebSearchAPIError(f"SearXNG returned non-JSON: {e}") from e

    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise WebSearchAPIError("SearXNG payload missing 'results' list")

    results: list[dict] = []
    for item in raw_results[:max_results]:
        results.append({
            "title": item.get("title", "").strip(),
            "url": item.get("url", "").strip(),
            "snippet": item.get("content", "").strip(),
            "engine": item.get("engine", ""),
        })

    log.info("web_search query=%r → %d results", query, len(results))
    return results


def web_fetch(url: str, max_chars: int = 8000) -> dict:
    """
    Fetch a URL and extract the main textual content.

    Uses trafilatura for boilerplate removal. Falls back to raw text if
    trafilatura is unavailable or extraction returns nothing.

    Returns a dict: {"url": str, "title": str, "text": str, "truncated": bool}.
    Raises WebFetchError on transport or extraction failures.
    """
    if not url or not url.strip():
        raise ValueError("url must be a non-empty string")
    if max_chars < 100:
        raise ValueError("max_chars must be >= 100")

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=DEFAULT_FETCH_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; kern-jarvis/2.0; "
                    "+https://github.com/siegelmaik-sketch/Kern-Jarvis-V2)"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as e:
        log.exception("web_fetch failed for url=%r", url)
        raise WebFetchError(f"Fetch failed: {e}") from e

    raw_html = response.content[:MAX_FETCH_BYTES].decode(
        response.encoding or "utf-8", errors="replace"
    )

    title = ""
    text = ""

    try:
        import trafilatura  # type: ignore[import-not-found]

        extracted = trafilatura.extract(
            raw_html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if extracted:
            text = extracted

        metadata = trafilatura.extract_metadata(raw_html)
        if metadata and metadata.title:
            title = metadata.title
    except ImportError:
        log.warning("trafilatura not installed — falling back to raw HTML")

    if not text:
        raise WebFetchError("Could not extract any text from URL")

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    return {
        "url": str(response.url),
        "title": title,
        "text": text,
        "truncated": truncated,
    }
