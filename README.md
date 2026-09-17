# Multi Source Data Scout

A data-collection pipeline that combines **browser-based web scraping** (Playwright, async, headless)
with a **public API** (Open Library), **evaluates the quality of each source**, and lands clean,
**deduplicated data into SQLite**.

This project exists to demonstrate a real data-engineering **source-evaluation methodology** — not
just "scraping for scrapes' sake". Every stage is instrumented: we respect robots.txt, throttle and
retry politely, grade each source across six dimensions (relevance, freshness, coverage, accessibility,
reliability, licensing), quarantine bad rows instead of silently dropping them, and re-run idempotently.
The pipeline is scheduled in CI every 6 hours and gates the build on the observed error rate.

---

## Why it exists

Anyone can write a scraper. The hard parts of production data engineering are:

1. **Source vetting** — is this source relevant, fresh, complete, accessible, and legal to use?
2. **Resilience** — the target will change/break; the pipeline must degrade gracefully and report it.
3. **Idempotency** — re-running must never duplicate rows.
4. **Accountability** — bad rows are quarantined with reasons, never vanished.

`multi-source-data-scout` exercises all of those against two public **scraping sandboxes**
(operated specifically to be scraped) plus a genuinely useful public API, and writes the resulting
source evaluation to `docs/source_evaluation.md` every run — the kind of doc a data engineer would
hand to a team on `#data-eng`.

---

## Architecture

```
                          +-----------------------------+
                          |     run_pipeline.py         |
                          |  entrypoint + CLI + logging |
                          +-------------+---------------+
                                        |
                                        v
                          +-------------+---------------+
                          |       pipeline.py           |
                          |  stage orchestration + gate |
                          +----+----+----+----+----+----+
                               |    |    |    |    |
        +----------------------+    |    |    |    +----------------------+
        v                           v    |    v                           v
 +---------------+          +---------------+ |               +---------------+
 | robots gate   |          | validation/   | |               | source eval   |
 | (robots.txt,  |          | quarantine    | |               | report ->     |
 |  RFC 9309)    |          | + upsert load | |               | source_eval.md|
 +-------+-------+          +-------+-------+ |               +-------+-------+
         |                          |         |                       |
         v                          v         v                       v
 +---------------+        +---------------+  |  +---------------+   +---------------+
 | scraper/      |        | storage/      |  |  | api_client/   |   | llm/ (opt.)  |
 | quotes.py     |        | sqlite schema |  |  | openlibrary    |  | Groq summary |
 | books.py      |        | upsert SQL    |  |  | + JSON cache  |   | feature-flag |
 +-------+-------+        +-------^-------+  |  +-------+-------+   +---------------+
         |                        |          |
         v                        |          v
 +-------------------------------+  +----------------------------------+
 | PlaywrightFetcher (async,     |  | OpenLibraryClient (rate-limited, |
 | 1-2s jittered delay, backoff) |  | cached, retries)                |
 +-------------------------------+  +----------------------------------+
         |                                   |
         |  quotes.toscrape.com/js  (books)  |  openlibrary.org (search.json, /api/books)
         |  books.toscrape.com               |
         v                                   v
   +--------------------------------------------+
   |            data/scout.db (SQLite)          |
   | books · quotes · api_lookups · quarantine  |
   | pipeline_runs                              |
   +--------------------------------------------+
```

---

## Repository layout

```
multi_source_data_scout/
├── scraper/        robots.txt gate, Playwright fetcher, pure HTML parsers, crawlers
├── api_client/     request-keyed JSON cache + Open Library client
├── validation/     field-level validators + quarantine classification
├── storage/        SQLite schema + idempotent upserts
├── evaluation/     generates docs/source_evaluation.md
├── llm/            optional Groq run summary (feature-flagged, off by default)
├── pipeline.py     orchestration (stages, metrics, error-rate gate)
config.py        all tunables + env-var wiring
models.py        shared record/result dataclasses
run_pipeline.py  CLI entrypoint
tests/           unit tests (validation, storage, mocked scraper, cache/API)
```

---

## Setup

Requires **Python 3.11+**.

```bash
# 1. virtualenv + dependencies (pinned in requirements.txt)
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. install the Playwright browser binaries (Chromium, headless)
playwright install chromium          # Linux CI: `playwright install --with-deps chromium`

# 3. (optional) configure env vars
cp .env.example .env                 # GROQ_API_KEY is optional — see below
```

### Optional environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | *(unset)* | **Optional.** Enables the one-paragraph LLM run summary. If absent the pipeline logs `LLM summary skipped (no API key configured)` and runs identically. |
| `SCOUT_LLM_SUMMARY` | auto | `0` disables the summary even when a key is present. |
| `SCOUT_API_LOOKUP_LIMIT` | `150` | Max Open Library lookups per run (politeness cap). |
| `SCOUT_ERROR_THRESHOLD` | `0.05` | Failed-rows ratio that fails the run (>5%). |

## Running locally

```bash
# full pipeline: robots gate → scrape → validate → quarantine → load → enrich → evaluate
python run_pipeline.py

# useful variations
python run_pipeline.py --verbose                # debug logging
python run_pipeline.py --no-scrape              # enrichment only (against existing DB)
python run_pipeline.py --no-enrich              # scrape/load only
python run_pipeline.py --limit 500              # allow more API lookups in one run
python run_pipeline.py --db data/other.db --threshold 0.1
python run_pipeline.py --no-llm-summary         # force-disable the LLM step
```

Every stage logs row counts, durations, and errors:

```
stage                  collected    valid   loaded  quaran.   errors      fail%
scrape.quotes               100        0        0        0        0       0.0%
scrape.books               1000        0        0        0        0       0.0%
validate.quotes             100      100        0        0        0       0.0%
validate.books             1000     1000        0        0        0       0.0%
load.quotes                 100      100      100        0        0       0.0%
load.books                 1000     1000     1000        0        0       0.0%
enrich.api                  150      150      150        0        0       0.0%
```

Run status is `OK` unless a stage's failed-rows ratio exceeds the threshold (then `run_pipeline.py`
exits `1`). Data lands in `data/scout.db`; the source evaluation lands in `docs/source_evaluation.md`.

### Re-running is idempotent

Verified live: a second run upserts `0 inserted / 100 updated` (quotes) and `0 inserted / 1000
updated` (books) — unique keys (`books.url`, `quotes.quote_id`, `api_lookups.(book_url, lookup_key)`)
with `ON CONFLICT ... DO UPDATE` mean no duplicates ever.

### API-cache behaviour

Records are keyed by request fingerprint in `data/api_cache/cache.json` (14-day TTL). Books already
enriched (`lookup_status IS NOT NULL`) and lookups under 30 days old are skipped, so by the time the
~1,000-title backlog is cross-referenced (around 8 scheduled runs at the default cap), subsequent runs
make **~0 live API calls** for enrichment.

---

## Scheduled CI

See `.github/workflows/pipeline.yml`.

| Trigger | Purpose |
|---|---|
| `cron: "0 */6 * * *"` | Data refresh every 6 hours. |
| `workflow_dispatch` | Manual run with one click. |
| `push` to `main` | Instant validation on every merge. |

What CI does:

1. Checks out, caches Playwright browsers, `pip install -r requirements.txt`, `playwright install --with-deps chromium`.
2. Runs `python run_pipeline.py --db data/scout.db` (with `set -o pipefail` so a gate breach fails the job).
3. Uploads `data/scout.db`, `docs/source_evaluation.md`, and `pipeline.log` as a build artifact (even on failure, via `if: always()`).
4. **Fails the build** if any stage exceeds the error-rate threshold (`>5% failed rows` by default) — the last step posts a clear `::error::` annotation.

> Tip: this pipeline fits perfectly on GitHub-hosted runners; each scheduled run takes ~6 minutes
> and performs ~90 polite, throttled HTTP requests against the sandboxes plus up to `--limit`
> API calls.

---

## Sample: `docs/source_evaluation.md`

Real output captured from a live full run. The file is regenerated by `run_pipeline.py` on every
run from the actual stage metrics, robots.txt findings, and — when a `GROQ_API_KEY` is present —
the Groq-generated natural-language summary:

```markdown
# Source Evaluation — multi-source-data-scout

_Generated 2026-09-17T07:28:49 UTC by the pipeline itself. Ratings below reflect static vetting + observations from the most recent run._

> Dimensions are evaluated per source: relevance, freshness / update cadence, coverage, accessibility (auth & rate limits), reliability (observed error rate & latency), and licensing / ToS. Verdicts: **Approved / Approved with caveats / Rejected**.

**Run summary**  
- Run status: **OK**
- Landing DB: `C:\Users\farhe\codesss\multi-source-data-scout\data\scout.db`
- Table counts: books=1000, quotes=100, api_lookups=750, quarantined=0

## Summary scorecard
| Source | Relevance | Freshness | Coverage | Accessibility | Reliability | Verdict |
|---|---|---|---|---|---|---|
| **Quotes to Scrape** | 5★ | 2★ | 5★ | 5★ | 5★ | Approved |
| **Books to Scrape** | 5★ | 2★ | 5★ | 5★ | 5★ | Approved |
| **Open Library API** | 5★ | 5★ | 4★ | 5★ | 4★ | Approved (capped) |

## 1. Quotes to Scrape
**Source URL:** https://quotes.toscrape.com/js/  
robots.txt: **robots.txt absent (HTTP 404) → allow-all per RFC 9309 §2.3.1**  
  - Detail: robots.txt returned HTTP 404 — nothing disallowed (RFC 9309 §2.3.1: allow-all on absence).

### Relevance
A curated collection of ~100 short quotes from ~50 authors with structured tags. Highly relevant as a **fixture dataset** for testing collection, cleaning, and enrichment logic. Not a primary business dataset, but ideal for exercising the /js/ (JS-rendered, browser-only) collection path that pure-HTTP scrapers cannot read.

### Freshness / update cadence
The corpus is static (Zyte's scraped-book demonstration app). The source is **not regularly updated**; we treat it as a low-churn reference dataset. Re-pulling on every scheduled run is cheap and proves that regressions in collection logic surface quickly, but it adds no new rows between updates.

### Coverage
Complete: we collect **all 10 paginated pages** (100 quotes), each with text, author, and tags. Cloudflare/firewall protections were not encountered. Tags cover the full embedded tag set; no truncation in the rendered DOM.

### Accessibility
Open to anonymous traffic. No API key or authentication. Rendering requires a real browser engine (Playwright) because quotes are injected client-side via `document.write` — hence we also implement a script-data fallback parser so a JS failure degrades gracefully. Rate limiting is not imposed, but we self-throttle to 1s–2s per request to stay polite.

### Reliability observed in this run
- Pages fetched: **10**
- Quotes collected: **100**
- Mean latency: **999.2 ms**
- Failed rows / pages: **0 (0.0%)**

### Licensing & ToS notes
Site is operated by Zyte for scraping education and states quotes are sourced from Goodreads. `/robots.txt` is not published (HTTP 404) → nothing is formally disallowed. Treat content as public demo data; authorship belongs to the original authors, and any redistribution must keep attribution.


**Verdict:** Approved — use as a stable fixture source
### Transport notes
- Total browser requests this run: **91**
- Failed requests: **0**
- Mean page load latency: **1429 ms**


## 2. Books to Scrape
**Source URL:** https://books.toscrape.com  
robots.txt: **robots.txt absent (HTTP 404) → allow-all per RFC 9309 §2.3.1**  
  - Detail: robots.txt returned HTTP 404 — nothing disallowed (RFC 9309 §2.3.1: allow-all on absence).

### Relevance
A ~1,000-title e-commerce catalogue (Django/Oscar demo) with title, price, star rating, and availability. **Directly relevant**: it gives us a bounded, realistic catalogue to union with Open Library metadata and to exercise price/rating parsing and dedup/upsert behaviour.

### Freshness / update cadence
Static sandbox catalogue (Zyte demo site). No meaningful update cadence; the same catalogue is served every run. Valuable for regression testing; not a live commercial feed.

### Coverage
We enumerate all **50 categories** from the sidebar (excluding the top-level aggregate to avoid double-scraping) and follow pagination through every page — i.e. **goal coverage of the full catalogue**. Category counts are not exposed in the sidebar (Django-Oscar layout), so completion is verified by pagination exhaustion.

### Accessibility
Fully anonymous, no auth. The site header literally advertises “We love being scraped!”. No IP throttling observed; we still hold to a 1–2s per-request delay and retry failures at most 3 times before aborting a category page.

### Reliability observed in this run
- Pages fetched: **80**
- Books collected: **1000**
- Mean latency: **1467.4 ms**
- Failed rows / pages: **0 (0.0%)**

### Licensing & ToS notes
Public demo/dummy catalogue built for scraping practice by Zyte (formerly Scrapinghub). `/robots.txt` returns 404 → allow-all per RFC 9309. Prices are in GBP (£) and are fictional demo data; they must not be marketed as real prices.


**Verdict:** Approved — use as the primary product-side dataset

## 3. Open Library API
**Source URL:** https://openlibrary.org  
robots.txt: **robots.txt present and parsed**  
  - Detail: robots.txt present at https://openlibrary.org/robots.txt

### Relevance
Open Library's public catalog metadata (100M+ editions) is the **authoritative cross-reference layer** — it lets us attach work IDs, authors, first-publish year, and community ratings to books scraped from Books-to-Scrape, and to measure how closely the sandbox catalogue maps to a real library catalog.

### Freshness / update cadence
Continuously populated by the Internet Archive community; new editions/records appear regularly. We refresh cached lookups only when they exceed a 30-day staleness window, so repeat runs impose near-zero load.

### Coverage
Lookups are **title-based** (listing pages expose no ISBN), capped at `SCOUT_API_LOOKUP_LIMIT` lookups per run for politeness. Because lookups are cached and only refreshed after 30 days, the whole backlog is cross-referenced over a handful of runs rather than in one burst. ISBN lookups are supported in the client but unused (no ISBN on scrape pages).

### Accessibility
Public API, **no API key required**; `/robots.txt` historically allows `/.*` for scripted access subject to a reasonable rate limit and a descriptive User-Agent (which we set). We self-throttle to 0.5s between calls and cache aggressively. The API can be flaky under load — retries with backoff are wired in.

### Reliability observed in this run
- Lookups attempted: **150**
- Failed rows / pages: **0 (0.0%)**

### Licensing & ToS notes
Open Library data is licensed under **CC0 / public-domain-friendly** terms for catalog metadata; the API itself is operated by the Internet Archive. Redistributing full bibliographic records should attribute the Internet Archive / Open Library per their data-dump terms.

- Lookups attempted: **150** (found=58, not_found=92, errors=0)  
- Cache hits this run: **1.0**  
- Req/s observed: **149** network requests, **0** failures, **1** cache hits  
**Verdict:** Approved — use as the enrichment/reference layer with rate caps

## Methodology notes

- Rows that fail validation are **quarantined**, not dropped: see the `quarantine` table.
- Re-runs are idempotent: unique keys (`books.url`, `quotes.quote_id`, `api_lookups.(book_url, lookup_key)`) mean upserts overwrite rather than duplicate.
- Reliability figures above use errors + quarantined rows over rows collected for each stage.
- Pipeline gate: any stage with failed ratio > **5%** marks the run as failed.


## LLM-assisted run summary (Groq)

The multi‑source‑data‑scout pipeline completed successfully, scraping 100 quotes and 1,000 books from the target sites with Playwright. Validation passed all 100 quotes and 1,000 books, and the load stages transferred 100 quotes and 1,000 books into SQLite with no errors or quarantines. The enrichment step queried the Open Library API for 150 records, returning 150 enriched entries in 98.8 s. All stages finished with a 0 % failure ratio and no notable anomalies were detected. The overall throughput met the 5 % threshold gate, confirming the run was within acceptable performance limits. The pipeline’s data integrity and load integrity were fully preserved across all stages.
```

The full document is in `docs/source_evaluation.md` after each run (and in every CI artifact).

---

## Tests

```bash
python -m pytest -q
```

- **`tests/test_validation.py`** — single-field validators + quarantine classification (no DB).
- **`tests/test_storage.py`** — idempotent upserts, quarantine landing, enrichment, lookup targeting — all against a temp SQLite DB.
- **`tests/test_crawlers.py`** — mocked scraper tests: a `FakeFetcher` serves saved HTML fixtures, so **no live site is ever touched** (CI-safe). Verifies pagination walking, robots denial, loop/error handling.
- **`tests/test_parsers.py`** — parsers run against real saved pages (including the JS/`var data` fallback path).
- **`tests/test_api_client.py`** — cache round-trips, TTL, and the Open Library client against a stubbed session (asserts the second identical call is served from cache).

---

## Ethics & ToS

This is a deliberately ethical scraping project:

- **Both scrape targets are public sandboxes built for scraping practice.**
  - `quotes.toscrape.com` and `books.toscrape.com` are demo apps operated by **Zyte** (formerly
    Scrapinghub) whose header literally says *"We love being scraped!"* on Books-to-Scrape.
  - The `books.toscrape.com` homepage states the site "is a demo website that is intended for
    learning purposes" — scraping practice is its intended use.
- **robots.txt is respected.** Both sandboxes return HTTP 404 for `/robots.txt`; per RFC 9309
  §2.3.1 that means nothing is disallowed, which we treat as allow-all **and record in the
  evaluation doc**. Open Library publishes a robots.txt, which we also parse and record. If a
  site ever publishes disallow rules we honour them (the gate sits before every request).
- **We are polite.** Randomized 1–2 s delay between page loads, exponential-backoff retries
  (max 3), capped API lookups per run (default 150), a JSON cache so repeat runs don't re-hit
  the API, and a descriptive contactable `User-Agent`.
- **We identify ourselves.** The `User-Agent` names the pipeline and links to this repository.
- **Content reuse.** Quotes belong to their original authors (we keep attribution); prices on
  Books-to-Scrape are fictional demo data; Open Library catalog metadata is CC0/public-domain
  friendly (we'd attribute the Internet Archive on redistribution).
- **Scope.** We fetch and store public catalog/demo content only — no login-gated data, no
  personal data, no bulk dumps beyond what the sandboxes invite.

---

## License

The pipeline code in this repository is MIT-licensed (see `LICENSE`, if present). Dataset content
belongs to its respective sources as documented above.
