-- Migration 0005: Per-card fodder detail rows

CREATE TABLE IF NOT EXISTS fodder_cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES fodder_snapshots(id),
    card_key        TEXT    NOT NULL DEFAULT '',
    player_name     TEXT    NOT NULL DEFAULT '',
    rating          INTEGER NOT NULL,
    position        TEXT    NOT NULL DEFAULT '',
    club_name       TEXT    NOT NULL DEFAULT '',
    nation_name     TEXT    NOT NULL DEFAULT '',
    club_badge_url  TEXT    NOT NULL DEFAULT '',
    nation_flag_url TEXT    NOT NULL DEFAULT '',
    card_version    TEXT    NOT NULL DEFAULT '',
    bin_price       INTEGER NOT NULL,
    rank_in_rating  INTEGER NOT NULL,
    ts_utc          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    platform        TEXT    NOT NULL CHECK(platform IN ('pc', 'console')),
    game_edition    TEXT    NOT NULL DEFAULT 'fc26'
);

CREATE INDEX IF NOT EXISTS idx_fodder_cards_snapshot_id
    ON fodder_cards (snapshot_id);

CREATE INDEX IF NOT EXISTS idx_fodder_cards_rating_platform_ts
    ON fodder_cards (rating, platform, ts_utc DESC);
