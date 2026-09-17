"""Crawler for https://quotes.toscrape.com/js/ (JS-rendered, paginated)."""

from __future__ import annotations

from urllib.parse import urljoin

from multi_source_data_scout.config import (
    MAX_PAGES_PER_CRAWL,
    QUOTES_BASE_URL,
)
from multi_source_data_scout.logutil import get_logger
from multi_source_data_scout.models import CrawlResult
from multi_source_data_scout.scraper.fetcher import AsyncFetcher
from multi_source_data_scout.scraper.parsers import ParseFailure, parse_quotes_page
from multi_source_data_scout.scraper.robots import RobotsChecker

log = get_logger("crawl.quotes")


class QuotesCrawler:
    """Politely follows pagination on the quotes sandbox."""

    def __init__(
        self,
        fetcher: AsyncFetcher,
        robots: RobotsChecker,
        base_url: str = QUOTES_BASE_URL,
        max_pages: int = MAX_PAGES_PER_CRAWL,
    ) -> None:
        self.fetcher = fetcher
        self.robots = robots
        self.base_url = base_url
        self.max_pages = max_pages

    async def run(self) -> CrawlResult:
        result = CrawlResult()
        result.detail["site"] = self.base_url

        url = self.base_url
        page_number = 1
        visited: set[str] = set()

        while url and page_number <= self.max_pages:
            if url in visited:
                log.warning("loop detected at %s — stopping", url)
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
                log.error("quotes page fetch failed: %s", exc)
                result.errors += 1
                result.detail.setdefault("first_error", str(exc))
                break

            try:
                parsed = parse_quotes_page(page.html, page.url, page_number=page_number)
            except ParseFailure as exc:
                log.error("quotes page %d unparseable (skipping): %s", page_number, exc)
                result.errors += 1
                break

            result.records.extend(parsed.records)
            if parsed.used_script_fallback:
                result.detail["used_script_fallback"] = True

            log.info(
                "page %d: %d quotes (%d skipped) latency=%.1fs",
                page_number,
                len(parsed.records),
                parsed.skipped,
                page.latency_seconds,
            )

            if parsed.next_relative_url:
                url = urljoin(page.url, parsed.next_relative_url)
                page_number += 1
            else:
                url = None

        result.detail["pages"] = result.pages
        if result.records:
            result.detail["authors"] = len({r.author for r in result.records})
            result.detail["tag_types"] = len({t for r in result.records for t in r.tags})
        if result.pages:
            result.detail["mean_latency_ms"] = round(
                result.detail["latency_sum"] / result.pages * 1000, 1
            )
        log.info(
            "quotes crawl complete: %d quotes across %d page(s), %d error(s)",
            len(result.records),
            page_number,
            result.errors,
        )
        return result