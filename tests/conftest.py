"""Test fixtures helpers + a static PageFetcher so scraper tests never hit
the network. Fixture files are saved real pages from the sandbox sites."""

from __future__ import annotations

from pathlib import Path

import pytest

from multi_source_data_scout.scraper.fetcher import FetchError, FetchedPage

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class FakeFetcher:
    """Serves fixture HTML from a URL -> html map; tracks requested URLs.

    Satisfies the AsyncFetcher protocol used by the crawlers.
    """

    def __init__(self, pages: dict[str, str], fail_on_missing: bool = True) -> None:
        self.pages = pages
        self.fail_on_missing = fail_on_missing
        self.requests: list[str] = []
        self.stats: dict[str, float] = {
            "pages_fetched": 0.0,
            "failures": 0.0,
            "latency_sum": 0.0,
            "latency_min": float("inf"),
            "latency_max": 0.0,
        }

    async def start(self) -> None:  # no-op for static fetcher
        pass

    async def stop(self) -> None:  # no-op for static fetcher
        pass

    async def fetch(self, url: str) -> FetchedPage:
        self.requests.append(url)
        if url in self.pages:
            self.stats["pages_fetched"] += 1
            self.stats["latency_sum"] += 0.05
            return FetchedPage(url=url, html=self.pages[url], status=200, latency_seconds=0.05, attempts=1)
        if self.fail_on_missing:
            self.stats["failures"] += 1
            raise FetchError(f"no fixture registered for {url}")
        return FetchedPage(url=url, html="", status=404, latency_seconds=0.01, attempts=1)


@pytest.fixture
def quote_pages() -> dict[str, str]:
    return {
        "https://quotes.toscrape.com/js/": load_fixture("quotes_js_page1.html"),
        "https://quotes.toscrape.com/js/page/2/": load_fixture("quotes_js_page2.html"),
        "https://quotes.toscrape.com/js/page/3/": "unused",  # page2 fixture has no 'next'
    }


@pytest.fixture
def books_pages() -> dict[str, str]:
    base = "https://books.toscrape.com/"
    cat1 = "https://books.toscrape.com/catalogue/category/books_1/index.html"
    cat2 = "https://books.toscrape.com/catalogue/category/books_1/page-2.html"
    return {
        base: load_fixture("books_index.html"),
        cat1: load_fixture("books_category_page1.html"),
        cat2: load_fixture("books_category_page2.html"),
    }