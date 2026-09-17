"""Unit tests for the validation logic and quarantine classification."""

from __future__ import annotations

import math

from multi_source_data_scout.models import ApiLookupRecord, BookRecord, QuoteRecord
from multi_source_data_scout.validation.validators import (
    classify,
    validate_book,
    validate_lookup,
    validate_quote,
)


def _quote(**overrides) -> QuoteRecord:
    data = dict(text="The unexamined life...", author="Socrates", tags=["philosophy"], url="https://q.t/page/1", page_number=1)
    data.update(overrides)
    return QuoteRecord(**data)


def _book(**overrides) -> BookRecord:
    data = dict(title="Clean Code", price=29.99, price_text="£29.99", rating=5, availability="In stock", category="Software", url="https://books.toscrape.com/catalogue/clean-code_1/index.html")
    data.update(overrides)
    return BookRecord(**data)


class TestQuoteValidation:
    def test_valid_quote_passes(self):
        assert validate_quote(_quote()) == []

    def test_empty_text_fails(self):
        issues = validate_quote(_quote(text="   "))
        assert any("text" in i for i in issues)

    def test_missing_author_fails(self):
        issues = validate_quote(_quote(author=""))
        assert any("author" in i for i in issues)

    def test_non_list_tags_fails(self):
        issues = validate_quote(_quote(tags="change"))
        assert any("tags" in i for i in issues)


class TestBookValidation:
    def test_valid_book_passes(self):
        assert validate_book(_book()) == []

    def test_missing_title_fails(self):
        issues = validate_book(_book(title=""))
        assert any("title" in i for i in issues)

    def test_non_numeric_price_fails(self):
        issues = validate_book(_book(price="£29.99"))
        assert any("price" in i for i in issues)

    def test_nan_price_fails(self):
        issues = validate_book(_book(price=float("nan")))
        assert any("price" in i for i in issues)

    def test_zero_or_negative_price_fails(self):
        assert any("price" in i for i in validate_book(_book(price=0)))
        assert any("price" in i for i in validate_book(_book(price=-1.5)))

    def test_rating_out_of_range_fails(self):
        assert any("rating" in i for i in validate_book(_book(rating=0)))
        assert any("rating" in i for i in validate_book(_book(rating=6)))
        assert validate_book(_book(rating=1)) == []
        assert validate_book(_book(rating=5)) == []

    def test_rating_not_integer_fails(self):
        assert any("rating" in i for i in validate_book(_book(rating=3.5)))

    def test_unexpected_availability_fails(self):
        issues = validate_book(_book(availability="Backordered"))
        assert any("availability" in i for i in issues)

    def test_top_of_range_valid(self):
        # sanity: extremes are fine
        assert validate_book(_book(rating=1, price=0.99)) == []


class TestLookupValidation:
    def test_valid_found_passes(self):
        rec = ApiLookupRecord(
            book_url="https://b/x", lookup_key="title:Clean Code", status="found",
            ol_work_id="OL123W", ol_edition_id="OL456M",
        )
        assert validate_lookup(rec) == []

    def test_found_without_identifiers_fails(self):
        rec = ApiLookupRecord(book_url="https://b/x", lookup_key="title:X", status="found")
        assert any("found" in i for i in validate_lookup(rec))

    def test_bad_status_fails(self):
        rec = ApiLookupRecord(book_url="https://b/x", lookup_key="t:A", status="maybe")
        assert any("status" in i for i in validate_lookup(rec))


class TestClassify:
    def test_splits_valid_and_invalid(self):
        quotes = [
            _quote(text="ok quote"),
            _quote(text="", author="missing text"),
            _quote(author=""),
        ]
        report = classify(quotes, validate_quote)
        assert len(report.valid) == 1
        assert len(report.invalid) == 2

    def test_invalid_rows_carry_reasons(self):
        report = classify([_book(price="abc")], validate_book)
        _, reasons = report.invalid[0]
        assert reasons and any("price" in r for r in reasons)

    def test_invalid_reason_counts(self):
        report = classify([_book(price="abc"), _book(title="")], validate_book)
        counts = report.invalid_reason_counts
        assert counts.get("price", 0) == 1
        assert counts.get("title", 0) == 1

    def test_no_rows(self):
        report = classify([], validate_quote)
        assert report.valid == []
        assert report.invalid == []