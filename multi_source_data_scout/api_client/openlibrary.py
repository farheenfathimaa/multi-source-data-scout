"""Open Library public API client (no API key required).

Endpoints used:
  * GET /search.json?title=...&fields=...   — title-based metadata search
  * GET /api/books?bibkeys=ISBN:...&jscmd=data — ISBN-based detail lookup

The client throttles itself (config.API_LOOKUP_DELAY_SECONDS between calls),
retries transient failures with backoff, and routes every request through the
JSON cache so repeat runs rarely touch the network.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

from multi_source_data_scout.api_client.cache import cache_key
from multi_source_data_scout.config import (
    API_LOOKUP_DELAY_SECONDS,
    API_RETRY_MAX,
    API_TIMEOUT_SECONDS,
    OPENLIBRARY_BASE_URL,
    USER_AGENT,
)
from multi_source_data_scout.logutil import get_logger
from multi_source_data_scout.models import ApiLookupRecord

log = get_logger("api.openlibrary")

# Keep responses small: only the fields the pipeline actually uses.
_SEARCH_FIELDS = (
    "key,title,author_name,first_publish_year,ratings_average,"
    "ratings_count,edition_key,subject"
)


class OpenLibraryError(Exception):
    """Raised when a lookup fails after retries."""


class OpenLibraryClient:
    def __init__(
        self,
        cache,
        *,
        user_agent: str = USER_AGENT,
        base_url: str = OPENLIBRARY_BASE_URL,
        timeout: float = API_TIMEOUT_SECONDS,
        max_retries: int = API_RETRY_MAX,
        delay_seconds: float = API_LOOKUP_DELAY_SECONDS,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._cache = cache
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.delay_seconds = delay_seconds
        self._last_request_at = 0.0
        self.stats: dict[str, float] = {"requests": 0.0, "failures": 0.0, "cache_hits": 0.0}

    # -- transport -----------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        url = f"{self.base_url}{endpoint}"
        request = {"endpoint": endpoint, "params": params}

        key = cache_key(request)
        cached = self._cache.get(key)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return {"_ok": True, "data": cached}

        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                self.stats["requests"] += 1
                resp.raise_for_status()
                data = resp.json()
                self._cache.set(key, data)
                return {"_ok": True, "data": data}
            except Exception as exc:
                self.stats["failures"] += 1
                if attempt > self.max_retries:
                    raise OpenLibraryError(f"{endpoint} failed after {attempt} attempts: {exc}") from exc
                time.sleep((2 ** attempt) + 0.25)

    # -- lookups -------------------------------------------------------------
    def search_by_title(self, title: str, limit: int = 1) -> list[dict]:
        """Search Open Library by title; returns matching docs (small)."""
        params = {
            "title": title,
            "limit": limit,
            "fields": _SEARCH_FIELDS,
        }
        result = self._get("/search.json", params)
        return list(result["data"].get("docs", []))

    def get_book_by_isbn(self, isbn: str) -> dict[str, Any]:
        """Metadata for a specific ISBN via the /api/books endpoint."""
        result = self._get(
            "/api/books",
            {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
        )
        data = result.get("data", {})
        key = f"ISBN:{isbn}"
        return data.get(key, {}) if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def _clean_title(title: str) -> str:
    return " ".join(title.replace("\u00a3", "").split())


def enrich_book(book_url: str, book_title: str, client: OpenLibraryClient) -> ApiLookupRecord:
    """Cross-reference a stored book against Open Library by title.

    Works from the landing row's url + title (books are already persisted
    before enrichment runs). Returns a lookup record; an `error` result never
    raises — the pipeline logs and continues.
    """
    title = _clean_title(book_title)
    lookup_key = f"title:{title[:120]}"
    try:
        docs = client.search_by_title(title, limit=1)
    except OpenLibraryError as exc:
        log.warning("OL lookup failed for %r: %s", book_title, exc)
        return ApiLookupRecord(
            book_url=book_url,
            lookup_key=lookup_key,
            status="error",
            error_message=str(exc),
        )
    if not docs:
        return ApiLookupRecord(book_url=book_url, lookup_key=lookup_key, status="not_found")

    doc = docs[0]
    authors = ", ".join(doc.get("author_name") or [])
    subjects = ", ".join((doc.get("subject") or [])[:6])
    editions = doc.get("edition_key") or []
    return ApiLookupRecord(
        book_url=book_url,
        lookup_key=lookup_key,
        status="found",
        ol_work_id=doc.get("key"),
        ol_edition_id=editions[0] if editions else None,
        ol_title=doc.get("title"),
        ol_authors=authors or None,
        ol_first_publish_year=doc.get("first_publish_year"),
        ol_ratings_average=doc.get("ratings_average"),
        ol_subjects=subjects or None,
    )