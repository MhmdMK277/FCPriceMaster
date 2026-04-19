"""
Seed 5 test cards so Electron reads can be verified before live scrapers exist.
Usage: uv run python -m backend.src.db.seed
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parents[4] / "data" / "fcpricemaster.db"

CARDS = [
    {
        "card_key": "mbappe-toty-fc26",
        "player_name": "Kylian Mbappé",
        "version_name": "TOTY",
        "game_edition": "fc26",
        "attributes": [
            ("rating", "99"),
            ("position", "ST"),
            ("league", "La Liga"),
            ("nation", "France"),
            ("club", "Real Madrid"),
            ("playstyle", "Finesse Shot"),
            ("playstyle_plus", "Technical"),
        ],
        "snapshots": [
            ("pc",      "2026-04-18T12:00:00Z", 4_800_000, 32),
            ("pc",      "2026-04-18T14:00:00Z", 4_750_000, 28),
            ("console", "2026-04-18T12:00:00Z", 5_100_000, 55),
            ("console", "2026-04-18T14:00:00Z", 5_050_000, 48),
        ],
    },
    {
        "card_key": "bellingham-tots-fc26",
        "player_name": "Jude Bellingham",
        "version_name": "TOTS",
        "game_edition": "fc26",
        "attributes": [
            ("rating", "97"),
            ("position", "CAM"),
            ("league", "La Liga"),
            ("nation", "England"),
            ("club", "Real Madrid"),
            ("playstyle", "Power Shot"),
        ],
        "snapshots": [
            ("pc",      "2026-04-18T12:00:00Z", 2_200_000, 60),
            ("console", "2026-04-18T12:00:00Z", 2_450_000, 95),
        ],
    },
    {
        "card_key": "salah-base-fc26",
        "player_name": "Mohamed Salah",
        "version_name": "Base",
        "game_edition": "fc26",
        "attributes": [
            ("rating", "90"),
            ("position", "RW"),
            ("league", "Premier League"),
            ("nation", "Egypt"),
            ("club", "Liverpool"),
        ],
        "snapshots": [
            ("pc",      "2026-04-18T12:00:00Z", 95_000, 200),
            ("console", "2026-04-18T12:00:00Z", 88_000, 310),
        ],
    },
    {
        "card_key": "haaland-potm-fc26",
        "player_name": "Erling Haaland",
        "version_name": "POTM",
        "game_edition": "fc26",
        "attributes": [
            ("rating", "93"),
            ("position", "ST"),
            ("league", "Premier League"),
            ("nation", "Norway"),
            ("club", "Manchester City"),
            ("playstyle", "Power Header"),
        ],
        "snapshots": [
            ("pc",      "2026-04-18T12:00:00Z", 420_000, 75),
            ("console", "2026-04-18T12:00:00Z", 390_000, 112),
        ],
    },
    {
        "card_key": "vinicius-futbirthday-fc26",
        "player_name": "Vinícius Jr.",
        "version_name": "FUT Birthday",
        "game_edition": "fc26",
        "attributes": [
            ("rating", "95"),
            ("position", "LW"),
            ("league", "La Liga"),
            ("nation", "Brazil"),
            ("club", "Real Madrid"),
            ("playstyle", "Rapid"),
            ("playstyle_plus", "Dribbling"),
        ],
        "snapshots": [
            ("pc",      "2026-04-18T12:00:00Z", 1_100_000, 45),
            ("console", "2026-04-18T12:00:00Z", 1_200_000, 68),
        ],
    },
]


def seed() -> None:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}. Run migrate first.")
        raise SystemExit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON;")

    seeded = 0
    for card in CARDS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO cards (card_key, player_name, version_name, game_edition) "
            "VALUES (?, ?, ?, ?)",
            (card["card_key"], card["player_name"], card["version_name"], card["game_edition"]),
        )
        if cur.lastrowid == 0:
            # already exists — look up the id
            row = conn.execute(
                "SELECT id FROM cards WHERE card_key = ?", (card["card_key"],)
            ).fetchone()
            card_id = row[0]
            print(f"  skip  (exists) {card['card_key']}")
        else:
            card_id = cur.lastrowid
            seeded += 1
            print(f"  insert card    {card['card_key']}  id={card_id}")

        for key, value in card["attributes"]:
            conn.execute(
                "INSERT OR IGNORE INTO card_attributes (card_id, key, value) VALUES (?, ?, ?)",
                (card_id, key, value),
            )

        for platform, ts_utc, bin_price, volume in card["snapshots"]:
            conn.execute(
                "INSERT INTO price_snapshots "
                "(card_id, platform, game_edition, ts_utc, bin_price, volume_proxy) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (card_id, platform, card["game_edition"], ts_utc, bin_price, volume),
            )

    conn.commit()
    conn.close()
    print(f"\nDone. {seeded} new card(s) inserted.")


if __name__ == "__main__":
    seed()
