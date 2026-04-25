-- Migration 0003: Twitter, Reddit, and EA news ingestion schema additions

ALTER TABLE signals ADD COLUMN signal_category TEXT;
ALTER TABLE signals ADD COLUMN priority        TEXT NOT NULL DEFAULT 'medium';

CREATE TABLE IF NOT EXISTS twitter_tweet_ids (
    tweet_id  TEXT    PRIMARY KEY,
    signal_id INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reddit_post_ids (
    post_id   TEXT    PRIMARY KEY,
    signal_id INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE
);
