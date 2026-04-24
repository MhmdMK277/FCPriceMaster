-- Migration 0002: Discord signal ingestion schema additions
-- Extends signals table and adds dedup + attachment tables for Discord.

ALTER TABLE signals ADD COLUMN source_server TEXT;
ALTER TABLE signals ADD COLUMN original_author TEXT;
ALTER TABLE signals ADD COLUMN original_ts_utc TEXT;
ALTER TABLE signals ADD COLUMN has_attachments INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS signal_attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id    INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    url          TEXT    NOT NULL,
    content_type TEXT,
    width        INTEGER,
    height       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_signal_attachments_signal ON signal_attachments(signal_id);

CREATE TABLE IF NOT EXISTS discord_message_ids (
    message_id TEXT    PRIMARY KEY,
    signal_id  INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE
);
