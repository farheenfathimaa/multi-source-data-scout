"""Mocked crawler tests — the FakeFetcher serves saved fixture HTML, so no
live site is ever contacted (matching CI constraints)."""

from __future__ import annotations

from multi_source_data_scout.models import Category
from multi_source_data_scout.scraper.books import BooksCrawler
from multi_source_data_scout.scraper.quotes import QuotesCrawler
from multi_source_data_scout.scraper.robots import RobotsChecker
from tests.conftest import FakeFetcher

QUOTES_BASE = "https://quotes.toscrape.com/js/"
BOOKS_BASE = "https://books.toscrape.com/"
CAT1 = "https://books.toscrape.com/catalogue/category/books_1/index.html"
CAT2 = "https://books.toscrape.com/catalogue/category/books_1/page-2.html"


async def test_quotes_crawler_follows_pagination(quote_pages):
    fetcher = FakeFetcher(quote_pages)
    crawler = QuotesCrawler(fetcher, RobotsChecker(), base_url=QUOTES_BASE)
    result = await crawler.run()

    assert len(result.records) == 20
    assert result.pages == 2
    assert result.errors == 0
    assert fetcher.requests == [QUOTES_BASE, f"{QUOTES_BASE}page/2/"]
    assert {r.author for r in result.records}  # authors extracted


async def test_quotes_crawler_handles_missing_page_gracefully(quote_pages):
    # page /3/ exists per fixture? No — fixture page2 has no next link, but to
    # exercise resilience, drop page2 so the crawl dies on a missing fixture.
    pages = {QUOTES_BASE: quote_pages[QUOTES_BASE]}
    fetcher = FakeFetcher(pages)
    crawler = QuotesCrawler(fetcher, RobotsChecker(), base_url=QUOTES_BASE)
    result = await crawler.run()

    assert len(result.records) == 10
    assert result.errors == 1
    assert result.pages == 1


async def test_books_crawler_walks_injected_category_pagination(books_pages):
    fetcher = FakeFetcher(books_pages)
    categories = [Category(name="Books", url=CAT1)]
    crawler = BooksCrawler(fetcher, RobotsChecker(), categories=categories)
    result = await crawler.run()

    assert len(result.records) == 40
    assert result.pages == 2
    assert result.errors == 0
    assert fetcher.requests == [CAT1, CAT2]
    assert all(r.category == "Books" for r in result.records)


async def test_books_crawler_zero_records_from_malformed_page(books_pages):
    # Category URL served unparseable HTML -> crawler survives, no crash.
    fetcher = FakeFetcher({CAT1: "<html><body>layout changed</body></html>"}, fail_on_missing=False)
    crawler = BooksCrawler(fetcher, RobotsChecker(), categories=[Category(name="Books", url=CAT1)])
    result = await crawler.run()

    assert result.records == []
    assert result.pages == 1
    # 0 records across pages is surfaced as a (possible) layout-change error
    assert result.errors == 1


async def test_books_crawler_flags_layout_change(books_pages):
    # 0 records across pages is treated as a possible layout change -> error
    fetcher = FakeFetcher({CAT1: "<html><body>empty</body></html>"}, fail_on_missing=False)
    crawler = BooksCrawler(fetcher, RobotsChecker(), categories=[Category(name="Books", url=CAT1)])
    result = await crawler.run()

    assert result.errors >= 1


async def test_books_category_discovery_from_sidebar(books_pages):
    fetcher = FakeFetcher({BOOKS_BASE: books_pages[BOOKS_BASE]})
    crawler = BooksCrawler(fetcher, RobotsChecker(), base_url=BOOKS_BASE)
    categories = await crawler._discover_categories()

    assert len(categories) == 50
    assert categories[0].name == "Travel"
    assert categories[0].url.startswith("https://books.toscrape.com/catalogue/category/")
    assert fetcher.requests == [BOOKS_BASE]
    assert fetcher.requests[0] == BOOKS_BASE


async def test_crawler_respects_robots_denial(quote_pages, monkeypatch):
    fetcher = FakeFetcher(quote_pages)
    robots = RobotsChecker()
    monkeypatch.setattr(robots, "_request_robots", lambda url: ("missing", None))
    await robots.check_site("quotes.toscrape.com", QUOTES_BASE)
    # deny everything for the quotes site
    robots._parsers["quotes.toscrape.com"] = _DenyAll()
    crawler = QuotesCrawler(fetcher, robots, base_url=QUOTES_BASE)
    result = await crawler.run()

    assert result.records == []
    assert result.pages_denied_by_robots == 1
    assert fetcher.requests == []  # never fetched


class _DenyAll:
    def can_fetch(self, *_: object) -> bool:
        return False