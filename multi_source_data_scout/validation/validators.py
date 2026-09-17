"""Field-level validation for incoming records.

Invalid rows are never silently dropped: `classify` splits them out and the
storage layer lands them in the `quarantine` table with the reasons attached.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from multi_source_data_scout.config import BOOKS_RATING_SCALE, VALID_AVAILABILITY
from multi_source_data_scout.models import ApiLookupRecord, BookRecord, QuoteRecord

Validator = Callable[[Any], "list[str]"]  # returns [] when valid


@dataclass
class ValidationIssue:
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass
class ValidationReport:
    valid: list[Any] = field(default_factory=list)
    invalid: list[tuple[Any, list[str]]] = field(default_factory=list)

    @property
    def invalid_reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, reasons in self.invalid:
            counts[reasons[0].split(":")[0]] = counts.get(reasons[0].split(":")[0], 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Individual validators — return a list of human-readable issues.
# ---------------------------------------------------------------------------

def validate_quote(record: QuoteRecord) -> list[str]:
    issues: list[str] = []
    if not record.text or not record.text.strip():
        issues.append("text: required field missing or empty")
    if not record.author or not record.author.strip():
        issues.append("author: required field missing or empty")
    if not isinstance(record.tags, list):
        issues.append("tags: expected a list")
    if not record.url:
        issues.append("url: required field missing")
    return issues


def validate_book(record: BookRecord) -> list[str]:
    issues: list[str] = []
    if not record.title or not record.title.strip():
        issues.append("title: required field missing or empty")
    if not isinstance(record.price, (int, float)) or isinstance(record.price, bool):
        issues.append("price: must be numeric")
    elif math.isnan(record.price):
        issues.append("price: not a number (parse failure)")
    elif record.price <= 0:
        issues.append(f"price: expected > 0, got {record.price!r}")
    if not isinstance(record.rating, int) or isinstance(record.rating, bool):
        issues.append(f"rating: expected integer {BOOKS_RATING_SCALE[0]}..{BOOKS_RATING_SCALE[1]}")
    elif not (BOOKS_RATING_SCALE[0] <= record.rating <= BOOKS_RATING_SCALE[1]):
        issues.append(f"rating: outside {BOOKS_RATING_SCALE[0]}..{BOOKS_RATING_SCALE[1]}, got {record.rating}")
    if record.availability not in VALID_AVAILABILITY:
        issues.append(f"availability: unexpected value {record.availability!r}")
    if not record.url:
        issues.append("url: required field missing")
    if not record.category:
        issues.append("category: required field missing")
    return issues


def validate_lookup(record: ApiLookupRecord) -> list[str]:
    issues: list[str] = []
    if record.status not in {"found", "not_found", "error"}:
        issues.append("status: unexpected value")
    if not record.book_url:
        issues.append("book_url: required field missing")
    if not record.lookup_key:
        issues.append("lookup_key: required field missing")
    if record.status == "found" and not (record.ol_work_id or record.ol_edition_id):
        issues.append("found: expected ol_work_id or ol_edition_id")
    return issues


# ---------------------------------------------------------------------------
# Classification helper
# ---------------------------------------------------------------------------

def classify(records: Iterable[Any], validator: Validator) -> ValidationReport:
    """Split records into valid / invalid lists, attaching failure reasons."""
    report = ValidationReport()
    for record in records:
        issues = validator(record)
        if issues:
            report.invalid.append((record, issues))
        else:
            report.valid.append(record)
    return report