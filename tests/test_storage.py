"""Tests for the upsert / quarantine / enrichment storage logic using a temp
SQLite database."""

from __future__ import annotations

import sqlite3

from multi_source_data_scout.models import (
    ApiLookupRecord,
    BookRecord,
    PipelineReport,
    QuoteRecord,
)
from multi_source_data_scout.storage.database import Database


def _books(n: int = 5) -> list[BookRecord]:
    return [
        BookRecord(
            title=f"Book {i}",
            price=10.0 + i,
            price_text=f"\u00a3{10.0 + i:.2f}",
            rating=5 if i % 2 else 3,
            availability="In stock",
            category="Test",
            url=f"https://books.toscrape.com/catalogue/book-{i}/index.html",
        )
        for i in range(n)
    ]


def _quotes(n: int = 5) -> list[QuoteRecord]:
    return [
        QuoteRecord(
            text=f"Quote number {i}",
            author="Author",
            tags=["a", "b"],
            url=f"https://quotes.toscrape.com/js/page/{i}",
            page_number=i,
        )
        for i in range(1, n + 1)
    ]


def test_schema_and_upsert_are_idempotent(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    inserted, updated = db.upsert_books(_books(), scraped_at="2026-01-01T00:00:00")
    assert (inserted, updated) == (5, 0)

    # Re-run of identical data: zero new rows, everything updated in place.
    inserted, updated = db.upsert_books(_books(), scraped_at="2026-01-02T00:00:00")
    assert (inserted, updated) == (0, 5)

    with sqlite3.connect(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 5


def test_quotes_upsert_idempotent(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    ins, upd = db.upsert_quotes(_quotes(), "2026-01-01T00:00:00")
    assert (ins, upd) == (5, 0)
    ins, upd = db.upsert_quotes(_quotes(), "2026-01-02T00:00:00")
    assert (ins, upd) == (0, 5)

    with sqlite3.connect(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 5


def test_upsert_refreshes_changed_values(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    books = _books(1)
    db.upsert_books(books, "2026-01-01T00:00:00")

    changed = books[0]
    books[0] = BookRecord(
        **{**changed.to_dict(), "price": 99.99}
    )
    db.upsert_books(books, "2026-01-02T00:00:00")

    with sqlite3.connect(tmp_path / "t.db") as conn:
        price = conn.execute("SELECT price FROM books WHERE url = ?", (books[0].url,)).fetchone()[0]
    assert price == 99.99


def test_quarantine_lands_rows_with_reasons(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    bad = QuoteRecord(text="", author="", tags=[], url="", page_number=3)
    n = db.quarantine_rows("quotes", [(bad, ["text: required field missing"])])
    assert n == 1

    with sqlite3.connect(tmp_path / "t.db") as conn:
        row = conn.execute("SELECT source, reason, payload FROM quarantine").fetchone()
    assert row[0] == "quotes"
    assert "text: required" in row[1]
    assert "url" in row[2]  # payload serialised


def test_api_lookup_upsert_and_duplicate_suppression(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    lookup = ApiLookupRecord(
        book_url="https://b/1", lookup_key="title:Book 1", status="found",
        ol_work_id="OL111W", ol_title="Book 1",
    )
    ins, upd = db.upsert_api_lookups([lookup])
    assert (ins, upd) == (1, 0)
    ins, upd = db.upsert_api_lookups([lookup])
    assert (ins, upd) == (0, 1)

    with sqlite3.connect(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_lookups").fetchone()[0] == 1


def test_enrichment_applied_to_books(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    books = _books(2)
    db.upsert_books(books, "2026-01-01T00:00:00")

    lookup = ApiLookupRecord(
        book_url=books[0].url, lookup_key=f"title:{books[0].title}", status="found",
        ol_work_id="OL777W", ol_title=books[0].title, ol_authors="Clean Code Guy",
    )
    updated = db.apply_enrichment([lookup])
    assert updated == 1

    with sqlite3.connect(tmp_path / "t.db") as conn:
        row = conn.execute("SELECT ol_work_id, ol_authors, lookup_status FROM books WHERE url = ?", (books[0].url,)).fetchone()
    assert row == ("OL777W", "Clean Code Guy", "found")


def test_books_needing_lookup_honours_limit_and_excludes_looked_up(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    db.upsert_books(_books(4), "2026-01-01T00:00:00")
    pending = db.books_needing_lookup(limit=2)
    assert len(pending) == 2  # capped

    lookup = ApiLookupRecord(
        book_url=pending[0][0], lookup_key=f"title:{pending[0][1]}", status="found"
    )
    db.upsert_api_lookups([lookup])
    db.apply_enrichment([lookup])

    pending_again = db.books_needing_lookup(limit=10)
    assert len(pending_again) == 3  # the enriched one no longer needs a lookup
    assert all(url != pending[0][0] for url, _ in pending_again)


def test_pipeline_run_bookkeeping(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    stage = sbiesp_stage()
    report = PipelineReport(stages=[stage], threshold=0.05)
    run_id = db.record_pipeline_run(report, "ok")
    assert run_id == 1

    with sqlite3.connect(tmp_path / "t.db") as conn:
        status = conn.execute("SELECT status FROM pipeline_runs WHERE id = 1").fetchone()[0]
    assert status == "ok"


def sbiesp_stage():
    from multi_source_data_scout.models import StageMetrics

    return StageMetrics("test.stage").finish()