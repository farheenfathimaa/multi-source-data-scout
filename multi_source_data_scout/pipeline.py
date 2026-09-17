"""Pipeline orchestration: robots gate -> scrape -> validate -> quarantine ->
load -> API enrichment -> evaluate -> optional LLM summary, all with
per-stage metrics and an error-rate gate."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from multi_source_data_scout import config
from multi_source_data_scout.api_client.cache import JsonFileCache
from multi_source_data_scout.api_client.openlibrary import (
    OpenLibraryClient,
    enrich_book,
)
from multi_source_data_scout.evaluation.report import write_evaluation
from multi_source_data_scout.llm.summary import summarize_run
from multi_source_data_scout.logutil import get_logger
from multi_source_data_scout.models import (
    PipelineReport,
    StageMetrics,
    now_iso,
)
from multi_source_data_scout.scraper.books import BooksCrawler
from multi_source_data_scout.scraper.fetcher import PlaywrightFetcher
from multi_source_data_scout.scraper.quotes import QuotesCrawler
from multi_source_data_scout.scraper.robots import RobotsChecker
from multi_source_data_scout.storage.database import Database
from multi_source_data_scout.validation.validators import (
    classify,
    validate_book,
    validate_quote,
)

log = get_logger("pipeline")


@dataclass
class RunOptions:
    db_path: Path
    cache_dir: Path
    scrape: bool = True
    enrich: bool = True
    error_threshold: float = config.ERROR_THRESHOLD
    api_lookup_limit: int = config.API_LOOKUP_LIMIT
    llm_summary: Optional[bool] = None  # None = auto-detect via GROQ_API_KEY


async def run_pipeline(opts: RunOptions) -> PipelineReport:
    report = PipelineReport(threshold=opts.error_threshold)
    db = Database(opts.db_path)
    db.init_schema()
    cache = JsonFileCache(opts.cache_dir)

    robots = RobotsChecker(user_agent=config.USER_AGENT)
    await robots.ensure(
        {
            "quotes.toscrape.com": config.QUOTES_BASE_URL,
            "books.toscrape.com": config.BOOKS_BASE_URL,
            "openlibrary.org": config.OPENLIBRARY_BASE_URL + "/",
        }
    )

    fetcher: Optional[PlaywrightFetcher] = None
    ol_client: Optional[OpenLibraryClient] = None
    scraped_at = now_iso()
    quote_records: list = []
    book_records: list = []

    try:
        # ------------------------------------------------------------------
        # Scrape stages (browser automation)
        # ------------------------------------------------------------------
        if opts.scrape:
            fetcher = PlaywrightFetcher(user_agent=config.USER_AGENT)
            await fetcher.start()

            quotes_crawl = await QuotesCrawler(fetcher, robots).run()
            report.stages.append(
                StageMetrics("scrape.quotes").updates(
                    rows_collected=len(quotes_crawl.records),
                    errors=quotes_crawl.errors,
                    detail=quotes_crawl.detail,
                ).finish()
            )
            quote_records = quotes_crawl.records

            books_crawl = await BooksCrawler(fetcher, robots).run()
            report.stages.append(
                StageMetrics("scrape.books").updates(
                    rows_collected=len(books_crawl.records),
                    errors=books_crawl.errors,
                    detail=books_crawl.detail,
                ).finish()
            )
            book_records = books_crawl.records
        else:
            report.stages.append(
                StageMetrics("scrape.quotes").updates(detail={"skipped": True}).finish()
            )
            report.stages.append(
                StageMetrics("scrape.books").updates(detail={"skipped": True}).finish()
            )

        # ------------------------------------------------------------------
        # Validation + quarantine
        # ------------------------------------------------------------------
        valid_quotes: list = []
        valid_books: list = []
        for source, records, validator, slot in (
            ("validate.quotes", quote_records, validate_quote, "quotes"),
            ("validate.books", book_records, validate_book, "books"),
        ):
            stage = StageMetrics(source)
            validation = classify(records, validator)
            stage.updates(
                rows_collected=len(records),
                rows_valid=len(validation.valid),
                rows_quarantined=len(validation.invalid),
            )
            report.stages.append(stage.finish())

            if validation.invalid:
                q_stage = StageMetrics("quarantine")
                q_stage.updates(
                    rows_quarantined=len(validation.invalid),
                    rows_loaded=len(validation.invalid),
                    detail={
                        "source": slot,
                        "reasons": validation.invalid_reason_counts,
                    },
                )
                db.quarantine_rows(slot, validation.invalid)
                report.stages.append(q_stage.finish())

            if slot == "quotes":
                valid_quotes = validation.valid
            else:
                valid_books = validation.valid

        # ------------------------------------------------------------------
        # Load (idempotent upserts)
        # ------------------------------------------------------------------
        lo = StageMetrics("load.quotes")
        ins, upd = db.upsert_quotes(valid_quotes, scraped_at)
        lo.updates(
            rows_collected=len(valid_quotes),
            rows_valid=len(valid_quotes),
            rows_loaded=ins + upd,
            detail={"inserted": ins, "updated": upd},
        )
        report.stages.append(lo.finish())

        lb = StageMetrics("load.books")
        ins, upd = db.upsert_books(valid_books, scraped_at)
        lb.updates(
            rows_collected=len(valid_books),
            rows_valid=len(valid_books),
            rows_loaded=ins + upd,
            detail={"inserted": ins, "updated": upd},
        )
        report.stages.append(lb.finish())

        # ------------------------------------------------------------------
        # API enrichment (Open Library)
        # ------------------------------------------------------------------
        ea = StageMetrics("enrich.api")
        if opts.enrich:  # enrich whatever the DB still needs, regardless of source
            ol_client = OpenLibraryClient(cache=cache)
            pending = db.books_needing_lookup(limit=opts.api_lookup_limit)
            lookups = []
            for book_url, book_title in pending:
                try:
                    lookups.append(enrich_book(book_url, book_title, ol_client))
                except Exception as exc:  # pragma: no cover - last-resort guard
                    ea.errors += 1
                    log.error("enrichment crashed for %r: %s", book_title, exc)

            statuses = {"found": 0, "not_found": 0, "error": 0}
            for lookup in lookups:
                statuses[lookup.status] = statuses.get(lookup.status, 0) + 1
            ea.updates(
                rows_collected=len(pending),
                rows_valid=statuses["found"] + statuses["not_found"],
                errors=statuses.get("error", 0),
                detail={
                    "looked_up": len(lookups),
                    "found": statuses["found"],
                    "not_found": statuses["not_found"],
                    "lookup_limit": opts.api_lookup_limit,
                    "api_requests": ol_client.stats.get("requests", 0),
                    "cache_hits": ol_client.stats.get("cache_hits", 0),
                    "requests_per_lookup": round(
                        ol_client.stats.get("requests", 0)
                        / max(len(lookups), 1),
                        3,
                    ),
                },
            )
            if lookups:
                db.upsert_api_lookups(lookups)
                db.apply_enrichment(lookups)
                ea.rows_loaded = len(lookups)
        else:
            ea.detail = {"skipped": True}
        report.stages.append(ea.finish())

        # ------------------------------------------------------------------
        # Optional LLM summary (generated before the doc so it can be embedded)
        # ------------------------------------------------------------------
        llm: bool = opts.llm_summary if opts.llm_summary is not None else config.LLM_SUMMARY_ENABLED
        if llm:
            report.llm_summary = summarize_run(report, os.environ.get("GROQ_API_KEY"))
        else:
            log.info("LLM summary skipped (no API key configured)")
        report.stages.append(
            StageMetrics("llm.summary")
            .updates(detail={"enabled": bool(llm), "generated": bool(report.llm_summary)})
            .finish()
        )

        # ------------------------------------------------------------------
        # Evaluation doc (embeds the LLM summary when present)
        # ------------------------------------------------------------------
        counts = db.counts()
        write_evaluation(
            report,
            counts,
            robots.policies(),
            config.EVALUATION_PATH,
            fetcher_stats=fetcher.stats if fetcher else {},
            api_stats=ol_client.stats if ol_client else {},
            db_path=str(opts.db_path),
        )
        report.stages.append(
            StageMetrics("evaluate").updates(detail={"output": str(config.EVALUATION_PATH)}).finish()
        )

        status = "failed" if report.breaches_threshold else "ok"
        db.record_pipeline_run(report, status)
        if report.breaches_threshold:
            report.errors.append(
                "error-rate gate breached (see failed_ratio per stage in the log / sqlite)"
            )
        return report

    finally:
        if fetcher is not None:
            await fetcher.stop()