"""Browser-based scraping: robots.txt gate, polite Playwright fetcher,
pure HTML parsers, and crawlers for the quotes and books sandboxes."""

from multi_source_data_scout.scraper.fetcher import (
    AsyncFetcher,
    FetchedPage,
    FetchError,
    PlaywrightFetcher,
)
from multi_source_data_scout.scraper.robots import RobotsChecker
from multi_source_data_scout.scraper.books import BooksCrawler
from multi_source_data_scout.scraper.quotes import QuotesCrawler

__all__ = [
    "AsyncFetcher",
    "FetchedPage",
    "FetchError",
    "PlaywrightFetcher",
    "RobotsChecker",
    "BooksCrawler",
    "QuotesCrawler",
]