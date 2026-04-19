-- Migration 0001: initial schema
-- Applies the full schema defined in backend/src/db/schema.sql.

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_key        TEXT    NOT NULL UNIQUE,
    player_name     TEXT    NOT NULL,
    version_name    TEXT    NOT NULL,
    game_edition    TEXT    NOT NULL DEFAULT 'fc26',
    created_at_utc  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS card_attributes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id  INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    key      TEXT    NOT NULL,
    value    TEXT    NOT NULL,
    UNIQUE (card_id, key, value)
);

CREATE INDEX IF NOT EXISTS idx_card_attributes_card ON card_attributes(card_id);
CREATE INDEX IF NOT EXISTS idx_card_attributes_key  ON card_attributes(key);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id       INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    platform      TEXT    NOT NULL CHECK(platform IN ('pc', 'console')),
    game_edition  TEXT    NOT NULL DEFAULT 'fc26',
    ts_utc        TEXT    NOT NULL,
    bin_price     INTEGER,
    volume_proxy  INTEGER,
    source        TEXT    NOT NULL DEFAULT 'futgg'
);

CREATE INDEX IF NOT EXISTS idx_price_card_platform_ts
    ON price_snapshots(card_id, platform, ts_utc DESC);

CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,
    source_id    TEXT,
    ts_utc       TEXT    NOT NULL,
    signal_type  TEXT    NOT NULL,
    raw_text     TEXT,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts_utc DESC);

CREATE TABLE IF NOT EXISTS signal_card_tags (
    signal_id INTEGER NOT NULL REFERENCES signals(id)  ON DELETE CASCADE,
    card_id   INTEGER NOT NULL REFERENCES cards(id)    ON DELETE CASCADE,
    PRIMARY KEY (signal_id, card_id)
);

CREATE TABLE IF NOT EXISTS releases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    release_type     TEXT NOT NULL,
    expected_date    TEXT,
    confirmed        INTEGER NOT NULL DEFAULT 0,
    source_signal_id INTEGER REFERENCES signals(id),
    notes            TEXT,
    created_at_utc   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER NOT NULL REFERENCES cards(id),
    platform        TEXT    NOT NULL CHECK(platform IN ('pc', 'console')),
    ts_utc          TEXT    NOT NULL,
    call            TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    horizon_hours   INTEGER,
    target_price    INTEGER,
    reasoning       TEXT,
    source          TEXT    NOT NULL DEFAULT 'llm'
);

CREATE INDEX IF NOT EXISTS idx_recs_card ON recommendations(card_id, ts_utc DESC);

CREATE TABLE IF NOT EXISTS outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id   INTEGER NOT NULL REFERENCES recommendations(id),
    evaluated_at_utc    TEXT NOT NULL,
    price_at_call       INTEGER,
    price_now           INTEGER,
    verdict             TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS scraper_health (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source               TEXT    NOT NULL,
    run_at_utc           TEXT    NOT NULL,
    success              INTEGER NOT NULL,
    records_written      INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    last_error           TEXT,
    schema_diff          TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_source ON scraper_health(source, run_at_utc DESC);

CREATE TABLE IF NOT EXISTS _migrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL UNIQUE,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
