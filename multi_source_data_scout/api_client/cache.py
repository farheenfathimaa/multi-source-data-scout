"""Tiny, dependency-free JSON file cache keyed by request fingerprint.

Repeat pipeline runs hit the cache instead of the public API. Values are
treated as opaque JSON; TTL is enforced with a lightweight eviction on load.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multi_source_data_scout.config import API_CACHE_TTL_SECONDS


def cache_key(request: dict[str, Any]) -> str:
    """Deterministic fingerprint of a request (endpoint + params)."""
    payload = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class CacheEntry:
    created_at: float
    value: Any

    def expired(self, ttl: float) -> bool:
        return time.time() - self.created_at > ttl


class JsonFileCache:
    """A single-file JSON cache with an in-memory index.

    Writes are atomic (temp file + replace) so a crash mid-write cannot corrupt
    the cache.
    """

    def __init__(self, directory: str | Path, ttl_seconds: float = API_CACHE_TTL_SECONDS) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path = self.directory / "cache.json"
        self.ttl = ttl_seconds
        self._lock = threading.RLock()
        self._data: dict[str, CacheEntry] = self._load()
        self.hits = 0
        self.misses = 0
        self.expirations = 0

    def _load(self) -> dict[str, CacheEntry]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        entries: dict[str, CacheEntry] = {}
        for key, value in raw.items():
            if not isinstance(value, dict) or "created_at" not in value:
                continue
            entry = CacheEntry(created_at=value["created_at"], value=value.get("value"))
            if entry.expired(self.ttl):
                self.expirations += 1
                continue
            entries[key] = entry
        return entries

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None or entry.expired(self.ttl):
                if entry is not None:
                    self.expirations += 1
                    self._data.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = CacheEntry(created_at=time.time(), value=value)
            self._flush()

    def _flush(self) -> None:
        payload = {k: {"created_at": v.created_at, "value": v.value} for k, v in self._data.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._flush()

    def __len__(self) -> int:
        return len(self._data)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._data), "hits": self.hits, "misses": self.misses, "expirations": self.expirations}