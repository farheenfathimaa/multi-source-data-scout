"""Shared data models used across the pipeline.

Records are the contract between stages: scrapers emit records, validation
classifies them, storage persists them. Everything here is plain dataclasses
so stages stay decoupled from both scraper internals and the SQLite layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


def stable_hash(*parts: str) -> str:
    """Short, deterministic content hash used as a dedup key."""
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Scrape records
# ---------------------------------------------------------------------------

@dataclass
class QuoteRecord:
    text: str
    author: str
    tags: list[str]
    url: str
    page_number: int

    @property
    def quote_id(self) -> str:
        return stable_hash(self.text, self.author)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BookRecord:
    title: str
    price: float
    price_text: str
    rating: int  # 1..5 stars
    availability: str
    category: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Category:
    name: str
    url: str


# ---------------------------------------------------------------------------
# API lookup records
# ---------------------------------------------------------------------------

@dataclass
class ApiLookupRecord:
    book_url: str
    lookup_key: str  # e.g. "title:Its Only the Himalayas"
    status: str  # "found" | "not_found" | "error"
    source: str = "openlibrary"
    ol_work_id: str | None = None
    ol_edition_id: str | None = None
    ol_title: str | None = None
    ol_authors: str | None = None
    ol_first_publish_year: int | None = None
    ol_ratings_average: float | None = None
    ol_subjects: str | None = None
    error_message: str | None = None
    fetched_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Crawl / stage / run results
# ---------------------------------------------------------------------------

@dataclass
class CrawlResult:
    records: list[Any] = field(default_factory=list)
    pages: int = 0
    errors: int = 0
    pages_denied_by_robots: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageMetrics:
    stage: str
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    rows_collected: int = 0
    rows_valid: int = 0
    rows_quarantined: int = 0
    rows_loaded: int = 0
    errors: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def error_rate(self) -> float:
        """Fraction of collected rows that failed processing (0..1)."""
        if self.rows_collected == 0 and self.errors == 0:
            return 0.0
        if self.rows_collected == 0:
            return 1.0 if self.errors else 0.0
        return self.errors / self.rows_collected

    @property
    def failed_ratio(self) -> float:
        """Failed work (errors + quarantined rows) over collected rows (0..1)."""
        failures = self.errors + self.rows_quarantined
        if self.rows_collected == 0:
            return 1.0 if failures else 0.0
        return failures / self.rows_collected

    def finish(self) -> "StageMetrics":
        self.finished_at = datetime.now()
        return self

    def updates(
        self,
        *,
        rows_collected: int | None = None,
        rows_valid: int | None = None,
        rows_quarantined: int | None = None,
        rows_loaded: int | None = None,
        errors: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> "StageMetrics":
        """Chained setter for post-hoc metric updates."""
        if rows_collected is not None:
            self.rows_collected = rows_collected
        if rows_valid is not None:
            self.rows_valid = rows_valid
        if rows_quarantined is not None:
            self.rows_quarantined = rows_quarantined
        if rows_loaded is not None:
            self.rows_loaded = rows_loaded
        if errors is not None:
            self.errors = errors
        if detail is not None:
            self.detail = detail
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_seconds"] = round(self.duration_seconds, 2)
        d["error_rate"] = round(self.error_rate, 4)
        d["failed_ratio"] = round(self.failed_ratio, 4)
        return d


@dataclass
class PipelineReport:
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    stages: list[StageMetrics] = field(default_factory=list)
    threshold: float = 0.05
    llm_summary: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def breaches_threshold(self) -> bool:
        """True if any stage's failed ratio exceeds the configured threshold."""
        return any(
            stage.failed_ratio > self.threshold and stage.rows_collected > 0
            for stage in self.stages
        )

    def stage(self, name: str) -> StageMetrics | None:
        for s in self.stages:
            if s.stage == name:
                return s
        return None

    def finish(self) -> "PipelineReport":
        self.finished_at = datetime.now()
        return self


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)