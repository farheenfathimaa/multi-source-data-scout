"""Async HTML fetching via Playwright (headless Chromium) with politeness
controls: randomized 1-2s delay between requests, exponential-backoff retries,
and a protocol that lets tests swap in a static/stub fetcher.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from multi_source_data_scout.logutil import get_logger

log = get_logger("fetcher")


@dataclass
class FetchedPage:
    url: str
    html: str
    status: Optional[int]
    latency_seconds: float
    attempts: int


class FetchError(Exception):
    """Raised when a URL could not be fetched after all retries."""


class AsyncFetcher(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def fetch(self, url: str) -> FetchedPage: ...


class PlaywrightFetcher:
    """Renders pages in a real headless Chromium browser.

    Uses JS-rendering (Playwright). Sits behind the AsyncFetcher protocol so
    crawlers don't care whether the browser is real or mocked in tests.
    """

    def __init__(
        self,
        user_agent: str,
        delay_range: tuple[float, float] = (1.0, 2.0),
        max_retries: int = 3,
        timeout_ms: int = 30_000,
        headless: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.delay_range = delay_range
        self.max_retries = max_retries
        self.timeout_ms = timeout_ms
        self.headless = headless

        self.stats: dict[str, float] = {
            "pages_fetched": 0.0,
            "failures": 0.0,
            "latency_sum": 0.0,
            "latency_min": float("inf"),
            "latency_max": 0.0,
        }
        self._pw = None
        self._browser = None
        self._context = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

    async def stop(self) -> None:
        for closer in (
            getattr(self, "_context", None) and self._context.close,
            getattr(self, "_browser", None) and self._browser.close,
            getattr(self, "_pw", None) and self._pw.stop,
        ):
            if closer is not None:
                try:
                    await closer()
                except Exception:  # pragma: no cover - best-effort shutdown
                    pass
        self._pw = self._browser = self._context = None

    def _backoff_seconds(self, attempt: int) -> float:
        return (2 ** attempt) + random.uniform(0.0, 0.5)

    async def fetch(self, url: str) -> FetchedPage:
        if self._context is None:
            raise RuntimeError("PlaywrightFetcher.start() must be called first")

        # Politeness: jittered delay before every request.
        await asyncio.sleep(random.uniform(*self.delay_range))

        attempts = 0
        start = time.perf_counter()
        while True:
            attempts += 1
            page = await self._context.new_page()
            try:
                response = await page.goto(
                    url, wait_until="load", timeout=self.timeout_ms
                )
                html = await page.content()
                status = response.status if response is not None else None
                self.stats["pages_fetched"] += 1
                elapsed = time.perf_counter() - start
                if status is not None and status >= 400:
                    raise FetchError(f"HTTP {status} for {url}")
                self._record_latency(elapsed)
                return FetchedPage(
                    url=str(page.url) or url,
                    html=html,
                    status=status,
                    latency_seconds=round(elapsed, 3),
                    attempts=attempts,
                )
            except Exception as exc:
                self.stats["failures"] += 1
                if attempts > self.max_retries:
                    log.warning(
                        "gave up on %s after %d attempts: %s", url, attempts, exc
                    )
                    raise FetchError(f"{url} failed after {attempts} attempts: {exc}") from exc
                wait = self._backoff_seconds(attempts)
                log.info("retry %d/%d %s in %.1fs (%s)", attempts, self.max_retries, url, wait, exc)
                await asyncio.sleep(wait)
            finally:
                await page.close()

    def _record_latency(self, seconds: float) -> None:
        self.stats["latency_sum"] += seconds
        self.stats["latency_min"] = min(self.stats["latency_min"], seconds)
        self.stats["latency_max"] = max(self.stats["latency_max"], seconds)

    @property
    def mean_latency_ms(self) -> float:
        if self.stats["pages_fetched"] == 0:
            return 0.0
        return self.stats["latency_sum"] / self.stats["pages_fetched"] * 1000.0