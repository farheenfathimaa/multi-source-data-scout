"""Unit tests for the pure HTML parsers against saved fixture pages."""

from __future__ import annotations

import pytest

from multi_source_data_scout.scraper.parsers import (
    ParseFailure,
    parse_books_page,
    parse_books_sidebar,
    parse_quotes_page,
)
from tests.conftest import load_fixture

TRAVEL_URL = "https://books.toscrape.com/catalogue/category/books_1/index.html"
QUOTES_URL = "https://quotes.toscrape.com/js/"


class TestQuoteParsing:
    def test_page1_recovers_all_quotes_with_metadata(self):
        result = parse_quotes_page(load_fixture("quotes_js_page1.html"), QUOTES_URL, page_number=1)
        assert len(result.records) == 10
        first = result.records[0]
        assert first.text.startswith("\u201cThe world as we have created it")
        assert first.author == "Albert Einstein"
        assert "change" in first.tags and "deep-thoughts" in first.tags
        assert first.url == QUOTES_URL
        assert first.page_number == 1

    def test_raw_page_uses_script_fallback(self):
        # The raw HTML sent over the wire has NO rendered .quote divs; the
        # parser must recover them from the embedded `var data = [...]`.
        result = parse_quotes_page(load_fixture("quotes_js_page1.html"), QUOTES_URL, page_number=1)
        assert result.used_script_fallback is True
        assert len(result.records) == 10

    def test_next_link_followed_and_absent_on_last_page(self):
        page1 = parse_quotes_page(load_fixture("quotes_js_page1.html"), QUOTES_URL)
        assert page1.next_relative_url == "/js/page/2/"

        page2 = parse_quotes_page(
            load_fixture("quotes_js_page2.html"), "https://quotes.toscrape.com/js/page/2/", page_number=2
        )
        assert len(page2.records) == 10
        assert page2.next_relative_url is None

    def test_unicode_escapes_decoded(self):
        result = parse_quotes_page(load_fixture("quotes_js_page1.html"), QUOTES_URL)
        assert any("\u201c" in r.text for r in result.records)

    def test_layout_break_raises_parse_failure(self):
        with pytest.raises(ParseFailure):
            parse_quotes_page("<html><body><p>nothing here</p></body></html>", QUOTES_URL)


class TestBookParsing:
    def test_category_page_recovers_books_with_fields(self):
        result = parse_books_page(load_fixture("books_category_page1.html"), TRAVEL_URL, "Books")
        assert len(result.records) == 20
        first = result.records[0]
        assert first.title == "A Light in the Attic"
        assert first.price == pytest.approx(51.77)
        assert first.price_text == "\u00a351.77"
        assert first.rating == 3
        assert first.availability == "In stock"

    def test_book_urls_normalised_to_absolute(self):
        result = parse_books_page(load_fixture("books_category_page1.html"), TRAVEL_URL, "Books")
        urls = {r.url for r in result.records}
        assert all(u.startswith("https://books.toscrape.com/catalogue/") for u in urls)

    def test_pagination_next_to_relative_page2_then_end(self):
        p1 = parse_books_page(load_fixture("books_category_page1.html"), TRAVEL_URL, "Books")
        assert p1.next_relative_url == "page-2.html"
        p2 = parse_books_page(
            load_fixture("books_category_page2.html"),
            "https://books.toscrape.com/catalogue/category/books_1/page-2.html",
            "Books",
        )
        assert len(p2.records) == 20
        assert p2.next_relative_url is None

    def test_out_of_stock_availability_captured(self):
        result = parse_books_page(load_fixture("books_category_page1.html"), TRAVEL_URL, "Books")
        assert {r.availability for r in result.records} <= {"In stock", "Out of stock"}

    def test_broken_card_skipped_not_fatal(self):
        html = """
        <article class="product_pod">
            <h3><a href="good_1/index.html" title="Good Book">Good Book</a></h3>
            <p class="price_color">&pound;10.00</p>
            <p class="star-rating Five"></p>
            <p class="instock availability">In stock</p>
        </article>
        <article class="product_pod">
            <!-- title anchor intentionally missing -->
            <p class="price_color">&pound;5.00</p>
        </article>
        """
        result = parse_books_page(html, "https://books.toscrape.com/catalogue/page-x.html", "Test")
        assert len(result.records) == 1
        assert result.skipped == 1
        assert result.records[0].title == "Good Book"
        assert result.records[0].rating == 5

    def test_sidebar_lists_all_leaf_categories(self):
        sidebar = parse_books_sidebar(load_fixture("books_index.html"), "https://books.toscrape.com/")
        assert len(sidebar.categories) == 50
        assert sidebar.categories[0].name == "Travel"
        # The top-level 'Books' aggregate must be excluded (leaf categories only)
        assert "Books" not in {c.name for c in sidebar.categories}
        assert all(c.url for c in sidebar.categories)

    def test_sidebar_missing_raises(self):
        with pytest.raises(ParseFailure):
            parse_books_sidebar("<html><body>no sidebar</body></html>", "https://books.toscrape.com/")