"""Crawler for https://books.toscrape.com — all category pages, paginated."""

from __future__ import annotations

from urllib.parse import urljoin

from multi_source_data_scout.config import BOOKS_BASE_URL, MAX_PAGES_PER_CRAWL
from multi_source_data_scout.logutil import get_logger
from multi_source_data_scout.models import Category, CrawlResult
from multi_source_data_scout.scraper.fetcher import AsyncFetcher
from multi_source_data_scout.scraper.parsers import (
    ParseFailure,
    parse_books_page,
    parse_books_sidebar,
)
from multi_source_data_scout.scraper.robots import RobotsChecker

log = get_logger("crawl.books")


class BooksCrawler:
    """Discovers categories from the sidebar, then walks each category's pages.

    `categories` may be injected (tests / pinned config) to skip the sidebar
    discovery step.
    """

    def __init__(
        self,
        fetcher: AsyncFetcher,
        robots: RobotsChecker,
        base_url: str = BOOKS_BASE_URL,
        categories: list[Category] | None = None,
        max_pages: int = MAX_PAGES_PER_CRAWL,
    ) -> None:
        self.fetcher = fetcher
        self.robots = robots
        self.base_url = base_url
        self.categories = categories
        self.max_pages = max_pages

    async def _discover_categories(self) -> list[Category]:
        decision = self.robots.decision(self.base_url)
        if not decision.allowed:
            log.warning("[robots] denying home page %s — cannot discover categories", self.base_url)
            return []
        page = await self.fetcher.fetch(self.base_url)
        sidebar = parse_books_sidebar(page.html, self.base_url)
        categories = []
        for cat in sidebar.categories:
            categories.append(Category(name=cat.name, url=urljoin(page.url, cat.url)))
        log.info("discovered %d categories from sidebar", len(categories))
        return categories

    async def run(self) -> CrawlResult:
        result = CrawlResult()
        result.detail["site"] = self.base_url

        categories = self.categories
        if categories is None:
            try:
                categories = await self._discover_categories()
            except (ParseFailure, Exception) as exc:
                log.error("category discovery failed: %s", exc)
                result.errors += 1
                return result
        if not categories:
            log.warning("no categories to crawl")
            return result

        result.detail["categories"] = len(categories)
        empty_pages = 0

        for cat in categories:
            url = cat.url
            page_number = 1
            visited: set[str] = set()
            while url and page_number <= self.max_pages:
                if url in visited:
                    log.warning("loop detected at %s — stopping category %s", url, cat.name)
                    break
                visited.add(url)

                decision = self.robots.decision(url)
                if not decision.allowed:
                    log.info("[robots] denying %s : %s", url, decision.note)
                    result.pages_denied_by_robots += 1
                    break

                try:
                    page = await self.fetcher.fetch(url)
                    result.pages += 1
                    result.detail["latency_sum"] = round(
                        result.detail.get("latency_sum", 0.0) + page.latency_seconds, 3
                    )
                except Exception as exc:
                    log.error("category %s page %d fetch failed: %s", cat.name, page_number, exc)
                    result.errors += 1
                    break

                try:
                    parsed = parse_books_page(page.html, page.url, category=cat.name)
                except ParseFailure as exc:
                    log.error("category %s page %d unparseable: %s", cat.name, page_number, exc)
                    result.errors += 1
                    break

                result.records.extend(parsed.records)
                if not parsed.records:
                    empty_pages += 1

                if parsed.next_relative_url:
                    url = urljoin(page.url, parsed.next_relative_url)
                    page_number += 1
                else:
                    url = None

        # A site-wide layout change (or total scrape failure) shows up as "all
        # pages produced zero records"; surface it as an error, don't crash.
        if categories and result.pages > 0 and not result.records:
            msg = "0 books extracted across all category pages — possible layout change"
            log.error(msg)
            result.errors += 1

        result.detail["pages"] = result.pages
        result.detail["empty_pages"] = empty_pages
        if result.pages:
            result.detail["mean_latency_ms"] = round(
                result.detail["latency_sum"] / result.pages * 1000, 1
            )
        if result.records:
            result.detail["categories_with_books"] = len({r.category for r in result.records})
            result.detail["sellers"] = {r.availability for r in result.records}
        log.info(
            "books crawl complete: %d books across %d category page(s), %d error(s)",
            len(result.records),
            result.detail["pages"],
            result.errors,
        )
        return result