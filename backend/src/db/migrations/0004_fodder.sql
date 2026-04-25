-- Migration 0004: Fodder tracker, card tagger, LLM call log

-- Fodder price snapshots: cheapest cards by rating
CREATE TABLE IF NOT EXISTS fodder_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    rating              INTEGER NOT NULL,
    platform            TEXT    NOT NULL CHECK(platform IN ('pc', 'console')),
    ts_utc              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    cheapest_bin        INTEGER,
    second_cheapest_bin INTEGER,
    median_bin          INTEGER,
    game_edition        TEXT    NOT NULL DEFAULT 'fc26'
);

CREATE INDEX IF NOT EXISTS idx_fodder_snapshots_rating_platform_ts
    ON fodder_snapshots (rating, platform, ts_utc DESC);

-- Card name aliases for fuzzy text matching
CREATE TABLE IF NOT EXISTS card_aliases (
    alias   TEXT    PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id)
);

-- LLM call log (spend tracking + history)
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    model         TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL    NOT NULL DEFAULT 0.0,
    feature       TEXT    NOT NULL DEFAULT 'ask',
    input_text    TEXT,
    output_json   TEXT
);

-- Add tagger tracking column to signals
ALTER TABLE signals ADD COLUMN tagged_at TEXT;
