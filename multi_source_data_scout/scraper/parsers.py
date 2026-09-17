"""Pure HTML parsers — no I/O, easily unit-tested against saved fixtures.

Scrapers render pages in the browser and hand the resulting HTML here; parsers
produce records plus the relative URL of the follow-on page. Structural
problems raise ParseFailure or are skipped per-card with `skipped` counts so a
layout change degrades gracefully instead of crashing the run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from multi_source_data_scout.models import BookRecord, Category, QuoteRecord

_STAR_NAMES = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
_PRICE_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)")
_SCRIPT_DATA_RE = re.compile(r"var data\s*=\s*(\[[\s\S]*?\])\s*;", re.MULTILINE)


class ParseFailure(Exception):
    """Raised when a page structure cannot be parsed at all."""


@dataclass
class QuotePageResult:
    records: list[QuoteRecord] = field(default_factory=list)
    next_relative_url: Optional[str] = None
    used_script_fallback: bool = False
    skipped: int = 0


@dataclass
class BooksPageResult:
    records: list[BookRecord] = field(default_factory=list)
    next_relative_url: Optional[str] = None
    skipped: int = 0


@dataclass
class SidebarResult:
    categories: list[Category] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Quotes (https://quotes.toscrape.com/js/)
# ---------------------------------------------------------------------------

def _extract_quotes_from_script(soup: BeautifulSoup, url: str) -> list[QuoteRecord]:
    """Fallback for JS-rendered pages: quotes are also embedded as a JS
    `var data = [...]` array in the raw HTML, so we can survive a JS failure."""
    for script in soup.find_all("script"):
        content = script.string or ""
        match = _SCRIPT_DATA_RE.search(content)
        if not match:
            continue
        payload = json.loads(match.group(1))
        records = []
        for i, item in enumerate(payload, start=1):
            author = (item.get("author") or {}).get("name", "")
            text = item.get("text", "")
            tags = list(item.get("tags") or [])
            if not text or not author:
                continue
            records.append(QuoteRecord(text=text, author=author, tags=tags, url=url, page_number=0))
        return records
    return []


def parse_quotes_page(html: str, url: str, page_number: int = 0) -> QuotePageResult:
    """Parse one quotes page. Prefers the rendered DOM (div.quote); falls back
    to the embedded JS data array when the DOM has no quotes (JS-disabled)."""
    soup = BeautifulSoup(html, "html.parser")
    result = QuotePageResult()

    next_link = soup.select_one("li.next a")
    if next_link:
        result.next_relative_url = next_link.get("href")

    nodes = soup.select("div.quote")
    if not nodes:
        records = _extract_quotes_from_script(soup, url)
        if not records:
            raise ParseFailure("no quotes found in DOM or embedded script data")
        for rec in records:
            rec.page_number = page_number
        result.records = records
        result.used_script_fallback = True
        return result

    for node in nodes:
        text_el = node.select_one("span.text")
        author_el = node.select_one(".author")
        tags = [tag.get_text(strip=True) for tag in node.select(".tags a.tag")]
        text = text_el.get_text(strip=True) if text_el else ""
        author = author_el.get_text(strip=True) if author_el else ""
        if not text or not author:
            result.skipped += 1
            continue
        result.records.append(
            QuoteRecord(text=text, author=author, tags=tags, url=url, page_number=page_number)
        )
    return result


# ---------------------------------------------------------------------------
# Books (https://books.toscrape.com)
# ---------------------------------------------------------------------------

def _parse_rating(rating_p) -> Optional[int]:
    """Star rating is encoded as the token 'star-rating One..Five'."""
    if rating_p is None:
        return None
    classes = set(rating_p.get("class") or [])
    for name, value in _STAR_NAMES.items():
        if name.lower() in {c.lower() for c in classes}:
            return value
    matched = re.search(r"\b(One|Two|Three|Four|Five)\b", " ".join(classes), re.I)
    if matched:
        return _STAR_NAMES[matched.group(1).capitalize()]
    return None


def _card_to_book(card, base_url: str, category: str) -> BookRecord | None:
    title_el = card.select_one("h3 a") or card.select_one(".image_container a")
    if title_el is None:
        return None
    title = (title_el.get("title") or title_el.get_text(strip=True) or "").strip()
    href = title_el.get("href") or ""
    if not title or not href:
        return None

    price_el = card.select_one("p.price_color")
    availability_el = card.select_one("p.instock.availability") or card.select_one("p.availability")
    price_text = price_el.get_text(strip=True) if price_el else ""
    match = _PRICE_RE.search(price_text.replace(",", ".").replace("\u00a3", ""))
    price = float(match.group(1)) if match else float("nan")

    rating = _parse_rating(card.select_one("p.star-rating"))
    availability = availability_el.get_text(strip=True) if availability_el else ""

    return BookRecord(
        title=title,
        price=price,
        price_text=price_text,
        rating=rating if rating is not None else -1,
        availability=availability,
        category=category,
        url=urljoin(base_url, href),
    )


def parse_books_page(html: str, base_url: str, category: str) -> BooksPageResult:
    soup = BeautifulSoup(html, "html.parser")
    result = BooksPageResult()

    next_link = soup.select_one("li.next a")
    if next_link:
        result.next_relative_url = next_link.get("href")

    for card in soup.select("article.product_pod"):
        book = _card_to_book(card, base_url, category)
        if book is None:
            result.skipped += 1
            continue
        result.records.append(book)
    return result


def parse_books_sidebar(html: str, base_url: str) -> SidebarResult:
    """The sidebar lists every category; we take the leaf categories, which
    excludes the top-level 'Books' aggregate (avoids double-scraping books)."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".side_categories")
    if container is None:
        raise ParseFailure("books sidebar (.side_categories) not found — layout change?")
    links = container.select("ul ul li a")
    if not links:
        raise ParseFailure("no category links found in sidebar")

    categories = []
    for a in links:
        name = a.get_text(strip=True)
        href = a.get("href")
        if not href:
            continue
        categories.append(Category(name=name, url=href))
    return SidebarResult(categories=categories)