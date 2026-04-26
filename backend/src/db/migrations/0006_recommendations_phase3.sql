-- Phase 3: autonomous recommendations
-- 1. Recreate recommendations with nullable card_id (fodder recs have no specific card)
--    and add dismissed_at for soft-delete.
-- 2. Add ts_utc default and an extra index on ts_utc.

PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS recommendations_v2 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER REFERENCES cards(id),
    platform        TEXT    NOT NULL CHECK(platform IN ('pc', 'console')),
    ts_utc          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    call            TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    horizon_hours   INTEGER,
    target_price    INTEGER,
    reasoning       TEXT,
    source          TEXT    NOT NULL DEFAULT 'llm',
    dismissed_at    TEXT
);

INSERT OR IGNORE INTO recommendations_v2 (id, card_id, platform, ts_utc, call, confidence, horizon_hours, target_price, reasoning, source)
    SELECT id, card_id, platform, ts_utc, call, confidence, horizon_hours, target_price, reasoning, source
    FROM recommendations;

DROP TABLE recommendations;
ALTER TABLE recommendations_v2 RENAME TO recommendations;

CREATE INDEX IF NOT EXISTS idx_recs_card ON recommendations(card_id, ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_recs_ts   ON recommendations(ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_outcomes_rec ON outcomes(recommendation_id);

PRAGMA foreign_keys=ON;
