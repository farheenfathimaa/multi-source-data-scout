-- multi-source-data-scout schema
-- Unique constraints are the backbone of idempotent upserts: re-running the
-- pipeline refreshes rows in place instead of duplicating them.

CREATE TABLE IF NOT EXISTS books (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT NOT NULL,
    price              REAL NOT NULL,
    price_text         TEXT,
    rating             INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    availability       TEXT NOT NULL,
    category           TEXT NOT NULL,
    url                TEXT NOT NULL UNIQUE,
    scraped_at         TEXT NOT NULL,
    -- Open Library enrichment columns (populated by the API stage)
    ol_work_id         TEXT,
    ol_edition_id      TEXT,
    ol_title           TEXT,
    ol_authors         TEXT,
    ol_first_publish_year INTEGER,
    ol_ratings_average REAL,
    ol_subjects        TEXT,
    lookup_status      TEXT,
    lookup_at          TEXT
);

CREATE TABLE IF NOT EXISTS quotes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id     TEXT NOT NULL UNIQUE,
    text         TEXT NOT NULL,
    author       TEXT NOT NULL,
    tags_json    TEXT NOT NULL DEFAULT '[]',
    url          TEXT NOT NULL,
    page_number  INTEGER NOT NULL,
    scraped_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_lookups (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id                INTEGER REFERENCES books(id) ON DELETE SET NULL,
    book_url               TEXT NOT NULL,
    lookup_key             TEXT NOT NULL,
    source                 TEXT NOT NULL DEFAULT 'openlibrary',
    status                 TEXT NOT NULL CHECK (status IN ('found','not_found','error')),
    ol_work_id             TEXT,
    ol_edition_id          TEXT,
    ol_title               TEXT,
    ol_authors             TEXT,
    ol_first_publish_year  INTEGER,
    ol_ratings_average     REAL,
    ol_subjects            TEXT,
    error_message          TEXT,
    fetched_at             TEXT NOT NULL,
    UNIQUE (book_url, lookup_key)
);

CREATE TABLE IF NOT EXISTS quarantine (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    reason     TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    stages_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_books_category     ON books(category);
CREATE INDEX IF NOT EXISTS idx_books_lookup_status ON books(lookup_status);
CREATE INDEX IF NOT EXISTS idx_quotes_author      ON quotes(author);
CREATE INDEX IF NOT EXISTS idx_lookups_book_url   ON api_lookups(book_url);
CREATE INDEX IF NOT EXISTS idx_quarantine_source  ON quarantine(source);