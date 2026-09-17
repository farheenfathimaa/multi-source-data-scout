"""Tests for the JSON cache and Open Library client using a stubbed session
(no network)."""

from __future__ import annotations

from multi_source_data_scout.api_client.cache import JsonFileCache, cache_key
from multi_source_data_scout.api_client.openlibrary import OpenLibraryClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.headers = {}
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return FakeResponse(self.payload)


class TestJsonFileCache:
    def test_roundtrip(self, tmp_path):
        cache = JsonFileCache(tmp_path / "cache")
        cache.set("k1", {"docs": [1, 2, 3]})
        assert cache.get("k1") == {"docs": [1, 2, 3]}
        assert cache.get("missing") is None

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "cache"
        JsonFileCache(path).set("a", "value-a")
        reloaded = JsonFileCache(path)
        assert reloaded.get("a") == "value-a"

    def test_histogram(self, tmp_path):
        cache = JsonFileCache(tmp_path / "cache")
        cache.set("k", "v")
        cache.get("k")
        cache.get("nope")
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_ttl_eviction(self, tmp_path):
        cache = JsonFileCache(tmp_path / "cache", ttl_seconds=-1)
        cache.set("old", "v")
        assert cache.get("old") is None

    def test_cache_key_deterministic_and_order_independent(self):
        assert cache_key({"a": 1, "b": 2}) == cache_key({"b": 2, "a": 1})
        assert cache_key({"a": 1}) != cache_key({"a": 2})


class TestOpenLibraryClient:
    def test_first_call_hits_network_second_reads_cache(self, tmp_path):
        payload = {"docs": [{"key": "OL1W", "title": "Clean Code"}]}
        session = FakeSession(payload)
        client = OpenLibraryClient(
            JsonFileCache(tmp_path / "cache"), session=session, delay_seconds=0
        )

        docs = client.search_by_title("Clean Code", limit=1)
        assert docs == payload["docs"]
        assert len(session.calls) == 1

        # Second identical lookup must be served from cache — no new request.
        docs2 = client.search_by_title("Clean Code", limit=1)
        assert docs2 == payload["docs"]
        assert len(session.calls) == 1
        assert client.stats["cache_hits"] == 1

    def test_different_queries_are_distinct_cache_keys(self, tmp_path):
        session = FakeSession({"docs": []})
        client = OpenLibraryClient(JsonFileCache(tmp_path / "cache"), session=session, delay_seconds=0)
        client.search_by_title("One")
        client.search_by_title("Two")
        assert len(session.calls) == 2

    def test_isbn_lookup_endpoint(self, tmp_path):
        payload = {"ISBN:9780132350884": {"title": "Clean Code", "publishers": ["Prentice Hall"]}}
        session = FakeSession(payload)
        client = OpenLibraryClient(JsonFileCache(tmp_path / "cache"), session=session, delay_seconds=0)

        meta = client.get_book_by_isbn("9780132350884")
        assert meta["title"] == "Clean Code"
        assert session.calls[0][1]["bibkeys"] == "ISBN:9780132350884"


def test_enrich_book_returns_error_lookup_on_api_failure(tmp_path, monkeypatch):
    from multi_source_data_scout.api_client import cache as cache_mod
    from multi_source_data_scout.api_client.openlibrary import OpenLibraryError, enrich_book

    class BoomSession:
        headers = {}

        def get(self, *args, **kwargs):
            raise OpenLibraryError("boom")

    client = OpenLibraryClient(cache_mod.JsonFileCache(tmp_path / "c"), session=BoomSession(), delay_seconds=0)
    lookup = enrich_book("https://b/1", "Missing Title", client)
    assert lookup.status == "error"
    assert "boom" in (lookup.error_message or "")