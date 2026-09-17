#!/usr/bin/env python3
"""Entrypoint: run the full multi-source-data-scout pipeline.

Stages execute in order with per-stage console logging (row counts, durations,
errors per stage). Exits non-zero when any stage's failed-rows ratio exceeds
the configured threshold (default 5%) — this is what CI watches.

Examples:
    python run_pipeline.py
    python run_pipeline.py --verbose
    python run_pipeline.py --db data/test.db --limit 20
    python run_pipeline.py --no-scrape            # enrichment only
    python run_pipeline.py --no-enrich            # scrape only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from multi_source_data_scout import __version__, config
from multi_source_data_scout.logutil import get_logger, setup_logging
from multi_source_data_scout.pipeline import RunOptions, run_pipeline

log = get_logger("main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="multi-source-data-scout",
        description="Scrape + API + validate + load data-collection pipeline with source evaluation.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", type=Path, default=config.DEFAULT_DB_PATH, help="SQLite landing DB path")
    parser.add_argument("--data-dir", type=Path, default=config.DEFAULT_DATA_DIR, help="data dir (db + cache)")
    parser.add_argument("--scrape", dest="scrape", action="store_true", default=True, help="run scraping stages (default)")
    parser.add_argument("--no-scrape", dest="scrape", action="store_false", help="skip scraping")
    parser.add_argument("--enrich", dest="enrich", action="store_true", default=True, help="run API enrichment (default)")
    parser.add_argument("--no-enrich", dest="enrich", action="store_false", help="skip API enrichment")
    parser.add_argument(
        "--limit",
        type=int,
        default=config.API_LOOKUP_LIMIT,
        help="max Open Library lookups per run",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=config.ERROR_THRESHOLD,
        help="failed-rows ratio that fails the run (default 0.05)",
    )
    parser.add_argument(
        "--llm-summary",
        dest="llm_summary",
        action="store_true",
        default=None,
        help="force LLM summary even without GROQ_API_KEY (will fail-fast if missing)",
    )
    parser.add_argument(
        "--no-llm-summary", dest="llm_summary", action="store_false", help="disable LLM summary"
    )
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(config.PROJECT_ROOT / ".env")
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

    opts = RunOptions(
        db_path=args.db,
        cache_dir=args.data_dir / "api_cache",
        scrape=args.scrape,
        enrich=args.enrich,
        error_threshold=args.threshold,
        api_lookup_limit=args.limit,
        llm_summary=args.llm_summary,
    )

    log.info("=" * 72)
    log.info("multi-source-data-scout %s — pipeline start", __version__)
    log.info("db=%s cache=%s scrape=%s enrich=%s threshold=%.0f%%",
             opts.db_path, opts.cache_dir, opts.scrape, opts.enrich, opts.error_threshold * 100)
    log.info("=" * 72)

    try:
        report = asyncio.run(run_pipeline(opts))
    except Exception as exc:  # pragma: no cover - unexpected crash
        log.exception("pipeline crashed: %s", exc)
        return 2

    log.info("=" * 72)
    log.info("%-22s %8s %8s %8s %8s %8s %10s", "stage", "collected", "valid", "loaded", "quaran.", "errors", "fail%")
    for stage in report.stages:
        if stage.detail.get("skipped"):
            log.info("%-22s %8s", stage.stage, "skipped")
            continue
        log.info(
            "%-22s %8d %8d %8d %8d %8d %9.1f%%",
            stage.stage,
            stage.rows_collected,
            stage.rows_valid,
            stage.rows_loaded,
            stage.rows_quarantined,
            stage.errors,
            stage.failed_ratio * 100,
        )
    log.info("total duration: %.1fs", report.duration_seconds)
    log.info("=" * 72)

    if report.breaches_threshold:
        for stage in report.stages:
            if stage.failed_ratio > opts.error_threshold and stage.rows_collected > 0:
                log.error(
                    "GATE BREACHED in stage '%s' (failed ratio %.1f%% > %.0f%%)",
                    stage.stage, stage.failed_ratio * 100, opts.error_threshold * 100,
                )
        log.error("run FAILED — error-rate gate exceeded threshold")
        return 1

    log.info("run OK — data in %s, evaluation at %s", opts.db_path, config.EVALUATION_PATH)
    if report.llm_summary:
        log.info("LLM run summary written to docs/source_evaluation.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())