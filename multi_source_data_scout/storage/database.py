"""SQLite persistence layer.

Upserts rely on the UNIQUE constraints from schema.sql plus
`INSERT ... ON CONFLICT ... DO UPDATE`, which makes re-runs idempotent.
Inserted-vs-updated counts come from comparing against pre-existing keys so
stage metrics are accurate without an extra round-trip.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from multi_source_data_scout.logutil import get_logger
from multi_source_data_scout.models import (
    ApiLookupRecord,
    BookRecord,
    PipelineReport,
    QuoteRecord,
    json_dumps,
    now_iso,
)

log = get_logger("storage")

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

_ENRICHMENT_COLUMNS = (
    "ol_work_id",
    "ol_edition_id",
    "ol_title",
    "ol_authors",
    "ol_first_publish_year",
    "ol_ratings_average",
    "ol_subjects",
    "lookup_status",
    "lookup_at",
)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- connection ----------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))

    # -- helpers ---------------------------------------------------------------
    def _existing_keys(self, table: str, column: str, keys: Iterable[str]) -> set[str]:
        """Keys already present in the table (used to count insert vs update)."""
        keys = list(keys)
        if not keys:
            return set()
        existing: set[str] = set()
        with self.connect() as conn:
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IN ({placeholders})",
                    chunk,
                ).fetchall()
                existing.update(r[column] for r in rows)
        return existing

    # -- upserts ----------------------------------------------------------------
    def upsert_books(
        self, records: list[BookRecord], scraped_at: str
    ) -> tuple[int, int]:
        """Returns (inserted, updated). Idempotent on books.url."""
        if not records:
            return 0, 0
        existing = self._existing_keys("books", "url", (r.url for r in records))
        inserted = updated = 0
        with self.connect() as conn:
            for record in records:
                row = (
                    record.title,
                    record.price,
                    record.price_text,
                    record.rating,
                    record.availability,
                    record.category,
                    record.url,
                    scraped_at,
                )
                conn.execute(
                    """
                    INSERT INTO books
                        (title, price, price_text, rating, availability,
                         category, url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        title = excluded.title,
                        price = excluded.price,
                        price_text = excluded.price_text,
                        rating = excluded.rating,
                        availability = excluded.availability,
                        category = excluded.category,
                        scraped_at = excluded.scraped_at
                    """,
                    row,
                )
                if record.url in existing:
                    updated += 1
                else:
                    inserted += 1
        log.info("books upsert: %d inserted, %d updated", inserted, updated)
        return inserted, updated

    def upsert_quotes(
        self, records: list[QuoteRecord], scraped_at: str
    ) -> tuple[int, int]:
        """Returns (inserted, updated). Idempotent on quotes.quote_id."""
        if not records:
            return 0, 0
        existing = self._existing_keys("quotes", "quote_id", (r.quote_id for r in records))
        inserted = updated = 0
        with self.connect() as conn:
            for record in records:
                row = (
                    record.quote_id,
                    record.text,
                    record.author,
                    json.dumps(record.tags, ensure_ascii=False),
                    record.url,
                    record.page_number,
                    scraped_at,
                )
                conn.execute(
                    """
                    INSERT INTO quotes
                        (quote_id, text, author, tags_json, url, page_number, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(quote_id) DO UPDATE SET
                        text = excluded.text,
                        author = excluded.author,
                        tags_json = excluded.tags_json,
                        url = excluded.url,
                        page_number = excluded.page_number,
                        scraped_at = excluded.scraped_at
                    """,
                    row,
                )
                if record.quote_id in existing:
                    updated += 1
                else:
                    inserted += 1
        log.info("quotes upsert: %d inserted, %d updated", inserted, updated)
        return inserted, updated

    def upsert_api_lookups(
        self, records: list[ApiLookupRecord]
    ) -> tuple[int, int]:
        """Returns (inserted, updated). Idempotent on (book_url, lookup_key)."""
        if not records:
            return 0, 0
        existing = self._existing_keys(
            "api_lookups",
            "book_url || lookup_key",
            (f"{r.book_url}{r.lookup_key}" for r in records),
        )
        inserted = updated = 0
        with self.connect() as conn:
            for record in records:
                row = (
                    record.book_url,
                    record.lookup_key,
                    record.source,
                    record.status,
                    record.ol_work_id,
                    record.ol_edition_id,
                    record.ol_title,
                    record.ol_authors,
                    record.ol_first_publish_year,
                    record.ol_ratings_average,
                    record.ol_subjects,
                    record.error_message,
                    record.fetched_at,
                )
                conn.execute(
                    """
                    INSERT INTO api_lookups
                        (book_url, lookup_key, source, status,
                         ol_work_id, ol_edition_id, ol_title, ol_authors,
                         ol_first_publish_year, ol_ratings_average, ol_subjects,
                         error_message, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(book_url, lookup_key) DO UPDATE SET
                        source = excluded.source,
                        status = excluded.status,
                        ol_work_id = excluded.ol_work_id,
                        ol_edition_id = excluded.ol_edition_id,
                        ol_title = excluded.ol_title,
                        ol_authors = excluded.ol_authors,
                        ol_first_publish_year = excluded.ol_first_publish_year,
                        ol_ratings_average = excluded.ol_ratings_average,
                        ol_subjects = excluded.ol_subjects,
                        error_message = excluded.error_message,
                        fetched_at = excluded.fetched_at
                    """,
                    row,
                )
                if f"{record.book_url}{record.lookup_key}" in existing:
                    updated += 1
                else:
                    inserted += 1
        log.info("api_lookups upsert: %d inserted, %d updated", inserted, updated)
        return inserted, updated

    # -- quarantine -------------------------------------------------------------
    def quarantine_rows(self, source: str, invalid: list[tuple[Any, list[str]]]) -> int:
        """Land invalid rows with their reasons (append-only log)."""
        with self.connect() as conn:
            for record, reasons in invalid:
                conn.execute(
                    """
                    INSERT INTO quarantine (source, reason, payload, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        source,
                        "; ".join(reasons),
                        json_dumps(record.to_dict() if hasattr(record, "to_dict") else record),
                        now_iso(),
                    ),
                )
        return len(invalid)

    # -- enrichment -----------------------------------------------------------
    def apply_enrichment(self, lookups: list[ApiLookupRecord]) -> int:
        """Copy lookup results onto the matching books rows."""
        updated = 0
        with self.connect() as conn:
            for lookup in lookups:
                lookup_at = lookup.fetched_at
                row = (
                    lookup.ol_work_id,
                    lookup.ol_edition_id,
                    lookup.ol_title,
                    lookup.ol_authors,
                    lookup.ol_first_publish_year,
                    lookup.ol_ratings_average,
                    lookup.ol_subjects,
                    lookup.status,
                    lookup_at,
                    lookup.book_url,
                )
                cur = conn.execute(
                    f"""
                    UPDATE books SET
                        ol_work_id = ?, ol_edition_id = ?, ol_title = ?,
                        ol_authors = ?, ol_first_publish_year = ?,
                        ol_ratings_average = ?, ol_subjects = ?,
                        lookup_status = ?, lookup_at = ?
                    WHERE url = ?
                    """,
                    row,
                )
                updated += cur.rowcount
        log.info("books enriched: %d rows updated", updated)
        return updated

    def books_needing_lookup(self, limit: int, stale_after_days: int = 30) -> list[tuple[str, str]]:
        """Book URLs (+ titles) without a recent lookup, oldest first.

        Once a book has any lookup it is returned only after `stale_after_days`
        pass — so repeat runs cost ~zero API calls.
        """
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT url, title FROM books b
                WHERE lookup_status IS NULL
                   OR lookup_at IS NULL
                   OR julianday(lookup_at) < julianday('now') - ?
                ORDER BY b.id
                LIMIT ?
                """,
                (stale_after_days, limit),
            ).fetchall()
        return [(r["url"], r["title"]) for r in rows]

    # -- run bookkeeping ---------------------------------------------------------
    def record_pipeline_run(self, report: PipelineReport, status: str) -> int:
        report.finish()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO pipeline_runs (started_at, finished_at, status, stages_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    report.started_at.isoformat(timespec="seconds"),
                    report.finished_at.isoformat(timespec="seconds"),
                    status,
                    json_dumps([s.to_dict() for s in report.stages]),
                ),
            )
            return cur.lastrowid

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "books": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
                "quotes": conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0],
                "api_lookups": conn.execute("SELECT COUNT(*) FROM api_lookups").fetchone()[0],
                "quarantined": conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0],
            }