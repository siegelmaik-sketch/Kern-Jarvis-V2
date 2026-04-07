"""
Kern-Jarvis V2 — Web Search & Fetch
═══════════════════════════════════
Web search via self-hosted SearXNG (central container in jarvis-shared network).
URL fetch via httpx + trafilatura for boilerplate-free content extraction.

Both functions are exposed as builtin tools through kern/tools.py.
Search results are cached in the web_cache table with a configurable TTL.
"""
import json
import logging
import time

import httpx

from kern.db import connection, get_config
from kern.exceptions import WebFetchError, WebSearchAPIError

log = logging.getLogger(__name__)

DEFAULT_SEARXNG_URL = "http://searxng:8080"
DEFAULT_LANGUAGE = "de"
DEFAULT_MAX_RESULTS = 5
DEFAULT_TIMEOUT = 15.0
DEFAULT_FETCH_TIMEOUT = 20.0
DEFAULT_CACHE_TTL = 3600  # 1 hour
MAX_FETCH_BYTES = 2_000_000  # 2 MB hard cap


def _cache_lookup(query: str, language: str, ttl: int) -> list[dict] | None:
    """Return cached results if fresh, else None. Cache misses and DB errors return None."""
    cutoff = int(time.time()) - ttl
    try:
        with connection() as conn:
            row = conn.execute(
                "SELECT results_json FROM web_cache "
                "WHERE query = ? AND language = ? AND created_at >= ?",
                (query, language, cutoff),
            ).fetchone()
    except Exception as e:
        log.warning("web_cache lookup failed: %s", e)
        return None

    if not row:
        return None
    try:
        return json.loads(row["results_json"])
    except json.JSONDecodeError as e:
        log.warning("web_cache row corrupt for query=%r: %s", query, e)
        return None


def _cache_store(query: str, language: str, results: list[dict]) -> None:
    """Persist results to cache. Failures are logged, never raised."""
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO web_cache (query, language, results_json, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(query, language) DO UPDATE SET "
                "results_json = excluded.results_json, created_at = excluded.created_at",
                (query, language, json.dumps(results), int(time.time())),
            )
            conn.commit()
    except Exception as e:
        log.warning("web_cache store failed: %s", e)


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[dict]:
    """
    Search the web via the central SearXNG instance.

    Cached for `web_cache_ttl` seconds (default 3600). The cache key is
    (query.strip(), language); max_results is applied AFTER cache lookup so
    different result counts share the same cache entry.

    Returns a list of dicts: {"title": str, "url": str, "snippet": str, "engine": str}.
    Raises WebSearchAPIError on transport failures or non-2xx responses.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if max_results < 1:
        raise ValueError("max_results must be >= 1")

    base = get_config("searxng_url", DEFAULT_SEARXNG_URL)
    language = get_config("search_language", DEFAULT_LANGUAGE)
    ttl = int(get_config("web_cache_ttl", str(DEFAULT_CACHE_TTL)))
    normalized_query = query.strip()

    # ── Cache lookup ──────────────────────────────────────────────────────
    if ttl > 0:
        cached = _cache_lookup(normalized_query, language, ttl)
        if cached is not None:
            log.info("web_search cache HIT query=%r → %d results", normalized_query, len(cached))
            return cached[:max_results]

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

    # Cache up to 20 results, but only return max_results to the caller.
    # This way a later call with a higher max_results can still hit the cache.
    cached_results: list[dict] = []
    for item in raw_results[:20]:
        cached_results.append({
            "title": item.get("title", "").strip(),
            "url": item.get("url", "").strip(),
            "snippet": item.get("content", "").strip(),
            "engine": item.get("engine", ""),
        })

    if ttl > 0 and cached_results:
        _cache_store(normalized_query, language, cached_results)

    log.info("web_search cache MISS query=%r → %d results (cached)",
             normalized_query, len(cached_results))
    return cached_results[:max_results]


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
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
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
