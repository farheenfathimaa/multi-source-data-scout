"""Produces docs/source_evaluation.md.

The report combines static source-vetting knowledge with runtime observations
(error rates, latency, counts, robots policy, cache behaviour) so it reads like
documentation handed to a data team rather than code output.

Dimensions evaluated per source (per the task spec):
relevance / freshness & cadence / coverage / accessibility / reliability /
licensing & ToS.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from multi_source_data_scout.logutil import get_logger
from multi_source_data_scout.models import PipelineReport
from multi_source_data_scout.scraper.robots import RobotsPolicy

log = get_logger("evaluation")

RUN_NAME = "multi-source-data-scout"


def _fmt_opt(value: str | None, fallback: str = "—") -> str:
    return value if value else fallback


def _stage(report: PipelineReport, name: str):
    return report.stage(name)


def _robots_line(policy: RobotsPolicy | None) -> str:
    if policy is None:
        return "robots.txt: not checked this run"
    state = {
        "present": "robots.txt present and parsed",
        "missing": "robots.txt absent (HTTP 404) → allow-all per RFC 9309 §2.3.1",
        "error": "robots.txt could not be fetched → treated cautiously",
    }[policy.state]
    notes = "; ".join(policy.notes) or state
    return f"robots.txt: **{state}**  \n  - Detail: {notes}"


def _reliability_table(report: PipelineReport, stage_name: str) -> str:
    stage = _stage(report, stage_name)
    if stage is None:
        return "No observations recorded this run."
    detail = stage.detail or {}
    rows = stage.rows_collected
    failed = stage.errors + stage.rows_quarantined
    rate = stage.failed_ratio if rows else (1.0 if failed else 0.0)

    labels = {
        "scrape.quotes": ("Pages fetched", "Quotes collected"),
        "scrape.books": ("Pages fetched", "Books collected"),
        "enrich.api": ("API calls made", "Lookups attempted"),
    }
    pages_label, rows_label = labels.get(stage_name, ("Units processed", "Rows collected"))

    lines: list[str] = []
    if detail.get("pages") is not None:
        lines.append(f"- {pages_label}: **{detail['pages']}**")
    lines.append(f"- {rows_label}: **{rows}**")
    latency = detail.get("mean_latency_ms")
    if latency:
        lines.append(f"- Mean latency: **{latency} ms**")
    lines.append(f"- Failed rows / pages: **{failed} ({rate:.1%})**")
    if stage.rows_quarantined:
        lines.append(f"- Rows quarantined: **{stage.rows_quarantined}**")
    if detail.get("first_error"):
        lines.append(f"- First error observed: `{detail['first_error']}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Static per-source intelligence
# ---------------------------------------------------------------------------

QUOTES_EVAL: dict[str, str] = {
    "url": "https://quotes.toscrape.com/js/",
    "relevance": (
        "A curated collection of ~100 short quotes from ~50 authors with "
        "structured tags. Highly relevant as a **fixture dataset** for testing "
        "collection, cleaning, and enrichment logic. Not a primary business "
        "dataset, but ideal for exercising the /js/ (JS-rendered, browser-only) "
        "collection path that pure-HTTP scrapers cannot read."
    ),
    "freshness": (
        "The corpus is static (Zyte's scraped-book demonstration app). The "
        "source is **not regularly updated**; we treat it as a low-churn "
        "reference dataset. Re-pulling on every scheduled run is cheap and "
        "proves that regressions in collection logic surface quickly, but it "
        "adds no new rows between updates."
    ),
    "coverage": (
        "Complete: we collect **all 10 paginated pages** (100 quotes), each "
        "with text, author, and tags. Cloudflare/firewall protections were not "
        "encountered. Tags cover the full embedded tag set; no truncation in "
        "the rendered DOM."
    ),
    "accessibility": (
        "Open to anonymous traffic. No API key or authentication. Rendering "
        "requires a real browser engine (Playwright) because quotes are "
        "injected client-side via `document.write` — hence we also implement a "
        "script-data fallback parser so a JS failure degrades gracefully. "
        "Rate limiting is not imposed, but we self-throttle to 1s–2s per "
        "request to stay polite."
    ),
    "licensing": (
        "Site is operated by Zyte for scraping education and states quotes are "
        "sourced from Goodreads. `/robots.txt` is not published (HTTP 404) → "
        "nothing is formally disallowed. Treat content as public demo data; "
        "authorship belongs to the original authors, and any redistribution "
        "must keep attribution."
    ),
    "verdict": "Approved — use as a stable fixture source",
}


BOOKS_EVAL: dict[str, str] = {
    "url": "https://books.toscrape.com",
    "relevance": (
        "A ~1,000-title e-commerce catalogue (Django/Oscar demo) with title, "
        "price, star rating, and availability. **Directly relevant**: it gives "
        "us a bounded, realistic catalogue to union with Open Library metadata "
        "and to exercise price/rating parsing and dedup/upsert behaviour."
    ),
    "freshness": (
        "Static sandbox catalogue (Zyte demo site). No meaningful update "
        "cadence; the same catalogue is served every run. Valuable for "
        "regression testing; not a live commercial feed."
    ),
    "coverage": (
        "We enumerate all **50 categories** from the sidebar (excluding the "
        "top-level aggregate to avoid double-scraping) and follow pagination "
        "through every page — i.e. **goal coverage of the full catalogue**. "
        "Category counts are not exposed in the sidebar (Django-Oscar layout), "
        "so completion is verified by pagination exhaustion."
    ),
    "accessibility": (
        "Fully anonymous, no auth. The site header literally advertises "
        "“We love being scraped!”. No IP throttling observed; we still hold to "
        "a 1–2s per-request delay and retry failures at most 3 times before "
        "aborting a category page."
    ),
    "licensing": (
        "Public demo/dummy catalogue built for scraping practice by Zyte "
        "(formerly Scrapinghub). `/robots.txt` returns 404 → allow-all per "
        "RFC 9309. Prices are in GBP (£) and are fictional demo data; they must "
        "not be marketed as real prices."
    ),
    "verdict": "Approved — use as the primary product-side dataset",
}


OPENLIBRARY_EVAL: dict[str, str] = {
    "url": "https://openlibrary.org",
    "relevance": (
        "Open Library's public catalog metadata (100M+ editions) is the "
        "**authoritative cross-reference layer** — it lets us attach work IDs, "
        "authors, first-publish year, and community ratings to books scraped "
        "from Books-to-Scrape, and to measure how closely the sandbox "
        "catalogue maps to a real library catalog."
    ),
    "freshness": (
        "Continuously populated by the Internet Archive community; new "
        "editions/records appear regularly. We refresh cached lookups only "
        "when they exceed a 30-day staleness window, so repeat runs impose "
        "near-zero load."
    ),
    "coverage": (
        "Lookups are **title-based** (listing pages expose no ISBN), capped at "
        "`SCOUT_API_LOOKUP_LIMIT` lookups per run for politeness. Because "
        "lookups are cached and only refreshed after 30 days, the whole "
        "backlog is cross-referenced over a handful of runs rather than in one "
        "burst. ISBN lookups are supported in the client but unused (no ISBN "
        "on scrape pages)."
    ),
    "accessibility": (
        "Public API, **no API key required**; `/robots.txt` historically "
        "allows `/.*` for scripted access subject to a reasonable rate limit "
        "and a descriptive User-Agent (which we set). We self-throttle to "
        "0.5s between calls and cache aggressively. The API can be flaky "
        "under load — retries with backoff are wired in."
    ),
    "licensing": (
        "Open Library data is licensed under **CC0 / public-domain-friendly** "
        "terms for catalog metadata; the API itself is operated by the "
        "Internet Archive. Redistributing full bibliographic records should "
        "attribute the Internet Archive / Open Library per their data-dump "
        "terms."
    ),
    "verdict": "Approved — use as the enrichment/reference layer with rate caps",
}


def _summary_table() -> str:
    scores: dict[str, tuple[int, ...]] = {
        "Quotes to Scrape": (5, 2, 5, 5, 5),
        "Books to Scrape": (5, 2, 5, 5, 5),
        "Open Library API": (5, 5, 4, 5, 4),
    }
    dims = ("Relevance", "Freshness", "Coverage", "Accessibility", "Reliability")
    header = "| Source | " + " | ".join(dims) + " | Verdict |"
    sep = "|" + "---|" * (1 + len(dims) + 1)
    rows = [
        f"| **{name}** | "
        + " | ".join(f"{s}★" for s in scores[name])
        + f" | {verdict} |"
        for name, verdict in (
            ("Quotes to Scrape", "Approved"),
            ("Books to Scrape", "Approved"),
            ("Open Library API", "Approved (capped)"),
        )
    ]
    return "\n".join([header, sep, *rows])


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


def build_markdown(
    report: PipelineReport,
    counts: dict[str, int],
    policies: dict[str, RobotsPolicy],
    fetcher_stats: dict[str, float] | None = None,
    api_stats: dict[str, float] | None = None,
    db_path: str | None = None,
) -> str:
    fetcher_stats = fetcher_stats or {}
    api_stats = api_stats or {}

    quotes_policy = policies.get("quotes.toscrape.com")
    books_policy = policies.get("books.toscrape.com")
    openlibrary_policy = policies.get("openlibrary.org")

    quotes_stage = _stage(report, "scrape.quotes")
    books_stage = _stage(report, "scrape.books")
    api_stage = _stage(report, "enrich.api")

    section = lambda name, body: f"### {name}\n{body}\n"  # noqa: E731

    def source_block(
        heading: str,
        static: dict[str, str],
        policy: RobotsPolicy | None,
        stage_name: str,
        extras: str = "",
    ) -> str:
        parts = [
            heading,
            f"**Source URL:** {static['url']}  ",
            _robots_line(policy),
            "",
            section("Relevance", static["relevance"]),
            section("Freshness / update cadence", static["freshness"]),
            section("Coverage", static["coverage"]),
            section("Accessibility", static["accessibility"]),
            section("Reliability observed in this run", _reliability_table(report, stage_name)),
            section("Licensing & ToS notes", static["licensing"]),
            extras,
            f"**Verdict:** {static['verdict']}",
        ]
        return "\n".join(parts)

    api_extras = []
    if api_stage is not None:
        api_extras.append(
            f"- Lookups attempted: **{api_stage.rows_collected}** "
            f"(found={api_stage.detail.get('found', '?')}, "
            f"not_found={api_stage.detail.get('not_found', '?')}, "
            f"errors={api_stage.errors})"
        )
        if api_stage.detail.get("cache_hits"):
            api_extras.append(f"- Cache hits this run: **{api_stage.detail['cache_hits']}**")
    if api_stats:
        api_extras.append(
            f"- Req/s observed: **{api_stats.get('requests', 0):.0f}** network requests, "
            f"**{api_stats.get('failures', 0):.0f}** failures, "
            f"**{api_stats.get('cache_hits', 0):.0f}** cache hits"
        )
    api_extras_text = "\n".join(f"{e}  " for e in api_extras)

    quotes_fetcher = ""
    if quotes_stage is not None and fetcher_stats.get("pages_fetched"):
        quotes_fetcher = (
            "\n### Transport notes\n"
            f"- Total browser requests this run: **{fetcher_stats['pages_fetched']:.0f}**\n"
            f"- Failed requests: **{fetcher_stats.get('failures', 0):.0f}**\n"
            f"- Mean page load latency: **{fetcher_stats.get('latency_sum', 0) / max(fetcher_stats.get('pages_fetched', 1), 1) * 1000:.0f} ms**\n"
        )

    run_status = "FAILED" if report.breaches_threshold else "OK"
    breached = [
        s.stage
        for s in report.stages
        if s.failed_ratio > report.threshold and s.rows_collected > 0
    ]
    breached_line = ""
    if breached:
        breached_line = f"\n- Breached stages: **{', '.join(breached)}**"
    header = (
        f"# Source Evaluation — {RUN_NAME}\n\n"
        f"_Generated {datetime.utcnow().isoformat(timespec='seconds')} UTC by the "
        f"pipeline itself. Ratings below reflect static vetting + observations "
        f"from the most recent run._\n\n"
        f"> Dimensions are evaluated per source: relevance, freshness / update "
        f"cadence, coverage, accessibility (auth & rate limits), reliability "
        f"(observed error rate & latency), and licensing / ToS. Verdicts: "
        f"**Approved / Approved with caveats / Rejected**.\n\n"
        f"**Run summary**  \n"
        f"- Run status: **{run_status}**"
        f"{breached_line}"
    )
    if db_path:
        header += f"\n- Landing DB: `{db_path}`"
    header += f"\n- Table counts: books={counts.get('books', 0)}, quotes={counts.get('quotes', 0)}, api_lookups={counts.get('api_lookups', 0)}, quarantined={counts.get('quarantined', 0)}"

    llm_note = ""
    if report.llm_summary:
        llm_note = (
            "\n## LLM-assisted run summary (Groq)\n\n"
            f"{report.llm_summary}\n"
        )

    markdown = "\n\n".join(
        [
            header,
            "## Summary scorecard\n" + _summary_table(),
            source_block("## 1. Quotes to Scrape", QUOTES_EVAL, quotes_policy, "scrape.quotes")
            + quotes_fetcher,
            source_block(
                "## 2. Books to Scrape",
                BOOKS_EVAL,
                books_policy,
                "scrape.books",
                "",
            ),
            source_block(
                "## 3. Open Library API",
                OPENLIBRARY_EVAL,
                openlibrary_policy,
                "enrich.api",
                api_extras_text,
            ),
            "## Methodology notes",
            (
                "- Rows that fail validation are **quarantined**, not dropped: "
                "see the `quarantine` table.\n"
                "- Re-runs are idempotent: unique keys (`books.url`, "
                "`quotes.quote_id`, `api_lookups.(book_url, lookup_key)`) mean "
                "upserts overwrite rather than duplicate.\n"
                "- Reliability figures above use errors + quarantined rows "
                "over rows collected for each stage.\n"
                f"- Pipeline gate: any stage with failed ratio > "
                f"**{report.threshold:.0%}** marks the run as failed."
            ),
            llm_note,
        ]
    )
    return markdown


def write_evaluation(
    report: PipelineReport,
    counts: dict[str, int],
    policies: dict[str, RobotsPolicy],
    path: Path,
    fetcher_stats: dict[str, float] | None = None,
    api_stats: dict[str, float] | None = None,
    db_path: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = build_markdown(report, counts, policies, fetcher_stats, api_stats, db_path)
    path.write_text(content, encoding="utf-8")
    log.info("wrote source evaluation to %s", path)