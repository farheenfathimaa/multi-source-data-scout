"""Central configuration for the pipeline.

All tunables live here (or in the environment) so the rest of the pipeline
reads one source of truth. Env vars are read lazily at import time and can be
overridden per-run via `run_pipeline.py` CLI flags (highest precedence).
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths ------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(__file__).resolve().parent

DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "scout.db"
DEFAULT_CACHE_DIR = DEFAULT_DATA_DIR / "api_cache"
DOCS_DIR = PROJECT_ROOT / "docs"
EVALUATION_PATH = DOCS_DIR / "source_evaluation.md"

# --- Scrape targets ----------------------------------------------------------
# Both targets are public scraping sandboxes operated by Zyte/SOFTLOFT —
# see README.md "Ethics & ToS".
QUOTES_BASE_URL = "https://quotes.toscrape.com/js/"
BOOKS_BASE_URL = "https://books.toscrape.com/"

# Descriptive, contactable User-Agent so servers can identify the pipeline.
USER_AGENT = (
    "Mozilla/5.0 (compatible; multi-source-data-scout/1.0; "
    "data-engineering source-evaluation pipeline; "
    "+https://github.com/example/multi-source-data-scout)"
)

# --- Politeness / resilience --------------------------------------------------
POLITE_DELAY_RANGE_SECONDS: tuple[float, float] = (1.0, 2.0)
RETRY_MAX = 3
REQUEST_TIMEOUT_MS = 30_000
MAX_PAGES_PER_CRAWL = 200  # hard circuit-breaker against runaway pagination

# --- Open Library API client ---------------------------------------------------
OPENLIBRARY_BASE_URL = "https://openlibrary.org"
API_TIMEOUT_SECONDS = 20.0
API_RETRY_MAX = 3
API_LOOKUP_DELAY_SECONDS = 0.5  # politeness gap between API calls
API_CACHE_TTL_SECONDS = 60 * 60 * 24 * 14  # cache lookups for 14 days


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None and raw.strip() != "" else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw is not None and raw.strip() != "" else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# --- Run shaping ----------------------------------------------------------------
# Max API-enrichment lookups executed per run; older lookups are refreshed
# before new ones so repeat runs mostly hit the JSON cache.
API_LOOKUP_LIMIT = _env_int("SCOUT_API_LOOKUP_LIMIT", 150)

# Fraction of failed rows tolerated. Stages that breach this cause
# run_pipeline.py to exit non-zero -> used by CI as a build gate.
ERROR_THRESHOLD = _env_float("SCOUT_ERROR_THRESHOLD", 0.05)

# LLM run summary: enabled when a GROQ_API_KEY is present (feature-flagged).
LLM_SUMMARY_ENABLED = _env_bool(
    "SCOUT_LLM_SUMMARY", os.environ.get("GROQ_API_KEY") is not None
)
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_TIMEOUT_SECONDS = 60

BOOKS_RATING_SCALE = (1, 5)
VALID_AVAILABILITY = {"In stock", "Out of stock"}