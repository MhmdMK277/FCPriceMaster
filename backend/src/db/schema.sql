-- FCPriceMaster schema
-- Ground truth for data model described in ARCHITECTURE.md
-- Applied via migrations/0001_initial.sql

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------------------
-- cards: master record per unique card (player + version)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_key        TEXT    NOT NULL UNIQUE,   -- stable slug, e.g. "mbappe-potm-fc26"
    player_name     TEXT    NOT NULL,
    version_name    TEXT    NOT NULL,          -- "TOTY", "TOTS", "Base", etc.
    game_edition    TEXT    NOT NULL DEFAULT 'fc26',  -- cross-FIFA: "fc26", "fc27"
    created_at_utc  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ---------------------------------------------------------------------------
-- card_attributes: tag-based key/value, schema-free for cross-FIFA portability
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS card_attributes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id  INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    key      TEXT    NOT NULL,   -- e.g. "rating", "position", "league", "playstyle"
    value    TEXT    NOT NULL,
    UNIQUE (card_id, key, value)
);

CREATE INDEX IF NOT EXISTS idx_card_attributes_card ON card_attributes(card_id);
CREATE INDEX IF NOT EXISTS idx_card_attributes_key  ON card_attributes(key);

-- ---------------------------------------------------------------------------
-- price_snapshots: time series per card + platform
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id       INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    platform      TEXT    NOT NULL CHECK(platform IN ('pc', 'console')),
    game_edition  TEXT    NOT NULL DEFAULT 'fc26',
    ts_utc        TEXT    NOT NULL,            -- ISO-8601
    bin_price     INTEGER,                    -- BIN (buy-it-now) price in coins; NULL if unavailable
    volume_proxy  INTEGER,                    -- e.g. number of listings observed
    source        TEXT    NOT NULL DEFAULT 'futgg'
);

CREATE INDEX IF NOT EXISTS idx_price_card_platform_ts
    ON price_snapshots(card_id, platform, ts_utc DESC);

-- ---------------------------------------------------------------------------
-- signals: everything from Discord, Twitter, Reddit, EA news, fixtures
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,   -- "discord", "twitter", "reddit", "ea_news", "fixture"
    source_id    TEXT,               -- external ID for dedup
    ts_utc       TEXT    NOT NULL,
    signal_type  TEXT    NOT NULL,   -- "leak", "sbc", "fixture", "promo", "meta", etc.
    raw_text     TEXT,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts_utc DESC);

-- signal ↔ card many-to-many
CREATE TABLE IF NOT EXISTS signal_card_tags (
    signal_id INTEGER NOT NULL REFERENCES signals(id)  ON DELETE CASCADE,
    card_id   INTEGER NOT NULL REFERENCES cards(id)    ON DELETE CASCADE,
    PRIMARY KEY (signal_id, card_id)
);

-- ---------------------------------------------------------------------------
-- releases: known/expected promos & SBCs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS releases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    release_type    TEXT NOT NULL,   -- "promo", "sbc", "objective"
    expected_date   TEXT,            -- ISO-8601 date (may be approximate)
    confirmed       INTEGER NOT NULL DEFAULT 0,  -- 0=rumoured, 1=confirmed
    source_signal_id INTEGER REFERENCES signals(id),
    notes           TEXT,
    created_at_utc  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ---------------------------------------------------------------------------
-- recommendations: LLM output
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER NOT NULL REFERENCES cards(id),
    platform        TEXT    NOT NULL CHECK(platform IN ('pc', 'console')),
    ts_utc          TEXT    NOT NULL,
    call            TEXT    NOT NULL,   -- "buy", "sell", "hold", "watch"
    confidence      REAL    NOT NULL,   -- 0.0–1.0
    horizon_hours   INTEGER,
    target_price    INTEGER,
    reasoning       TEXT,
    source          TEXT    NOT NULL DEFAULT 'llm'  -- "llm", "ask"
);

CREATE INDEX IF NOT EXISTS idx_recs_card ON recommendations(card_id, ts_utc DESC);

-- ---------------------------------------------------------------------------
-- outcomes: feedback loop for recommendation evaluation
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id   INTEGER NOT NULL REFERENCES recommendations(id),
    evaluated_at_utc    TEXT NOT NULL,
    price_at_call       INTEGER,
    price_now           INTEGER,
    verdict             TEXT,   -- "correct", "incorrect", "neutral", "expired"
    notes               TEXT
);

-- ---------------------------------------------------------------------------
-- scraper_health: per source run status
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scraper_health (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source               TEXT    NOT NULL,
    run_at_utc           TEXT    NOT NULL,
    success              INTEGER NOT NULL,   -- 1 or 0
    records_written      INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    last_error           TEXT,
    schema_diff          TEXT    -- populated when schema-guard fires
);

CREATE INDEX IF NOT EXISTS idx_health_source ON scraper_health(source, run_at_utc DESC);

-- ---------------------------------------------------------------------------
-- _migrations: internal migration tracker (created by migrate.py)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS _migrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL UNIQUE,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
