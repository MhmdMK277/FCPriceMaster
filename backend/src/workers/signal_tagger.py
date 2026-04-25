"""
Background signal tagger: fuzzy-matches signal raw_text against card_aliases
and populates signal_card_tags. Runs every 5 minutes via APScheduler.

Also seeds card_aliases from the cards table on first run.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = str(Path(__file__).parents[3] / "data" / "fcpricemaster.db")
_FUZZY_THRESHOLD = 85
_BATCH_SIZE = 50

# Hard-coded common nicknames — owner can add more via card_aliases table directly
_COMMON_NICKNAMES: dict[str, str] = {
    "mbappe": "Mbappé",
    "vini": "Vinícius Jr.",
    "vinicius": "Vinícius Jr.",
    "rodri": "Rodrigo",
    "bellingham": "Bellingham",
    "haaland": "Haaland",
    "salah": "Salah",
    "benzema": "Benzema",
    "neymar": "Neymar",
    "messi": "Messi",
    "ronaldo": "Ronaldo",
    "de bruyne": "De Bruyne",
    "pedri": "Pedri",
    "wirtz": "Wirtz",
    "yamal": "Yamal",
    "leao": "Leão",
    "raphinha": "Raphinha",
}


def seed_card_aliases(db_path: str) -> int:
    """
    Populate card_aliases from cards table if it's mostly empty.
    Returns number of aliases inserted.
    """
    con = sqlite3.connect(db_path)
    try:
        existing = con.execute("SELECT COUNT(*) FROM card_aliases").fetchone()[0]
        if existing > 100:
            return 0  # Already seeded

        cards = con.execute(
            "SELECT id, player_name, card_key FROM cards"
        ).fetchall()

        inserted = 0
        for card_id, player_name, card_key in cards:
            aliases_to_try = [
                player_name.lower(),
                player_name.lower().replace(" ", ""),
                # First name only (if multi-word and first part ≥4 chars)
            ]
            # First name
            parts = player_name.split()
            if parts and len(parts[0]) >= 4:
                aliases_to_try.append(parts[0].lower())
            # Last name
            if len(parts) >= 2 and len(parts[-1]) >= 4:
                aliases_to_try.append(parts[-1].lower())
            # card_key without prefix
            if "-" in card_key:
                aliases_to_try.append(card_key.split("-", 1)[1])

            for alias in aliases_to_try:
                alias = alias.strip()
                if len(alias) < 3:
                    continue
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO card_aliases (alias, card_id) VALUES (?,?)",
                        (alias, card_id),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass

        # Insert well-known nicknames where card exists
        for nickname, full_name in _COMMON_NICKNAMES.items():
            card_row = con.execute(
                "SELECT id FROM cards WHERE player_name LIKE ? LIMIT 1",
                (f"%{full_name}%",),
            ).fetchone()
            if card_row:
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO card_aliases (alias, card_id) VALUES (?,?)",
                        (nickname, card_row[0]),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass

        con.commit()
        logger.info("Seeded %d card aliases", inserted)
        return inserted
    finally:
        con.close()


def run_tagging(db_path: str, batch_size: int = _BATCH_SIZE) -> tuple[int, list[str]]:
    """
    Tag unprocessed signals. Returns (count_tagged, card_keys_newly_tagged).
    card_keys_newly_tagged is used by the async job to trigger on-demand price fetches.
    """
    try:
        from rapidfuzz import fuzz, process as rfprocess
    except ImportError:
        logger.error("rapidfuzz not installed — signal tagging unavailable. Run: uv add rapidfuzz")
        return 0

    con = sqlite3.connect(db_path)
    try:
        # Ensure aliases exist
        alias_count = con.execute("SELECT COUNT(*) FROM card_aliases").fetchone()[0]
        if alias_count == 0:
            seed_card_aliases(db_path)

        # Load all aliases into memory for fast lookup
        alias_rows = con.execute("SELECT alias, card_id FROM card_aliases").fetchall()
        aliases: dict[str, int] = {alias: card_id for alias, card_id in alias_rows}
        alias_keys = list(aliases.keys())

        if not alias_keys:
            return 0, []

        # Fetch untagged signals
        signals = con.execute(
            """SELECT id, raw_text FROM signals
               WHERE raw_text IS NOT NULL AND tagged_at IS NULL
               ORDER BY ts_utc DESC LIMIT ?""",
            (batch_size,),
        ).fetchall()

        tagged_count = 0
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        all_tagged_card_ids: set[int] = set()

        for signal_id, raw_text in signals:
            text_lower = raw_text.lower()
            # Tokenize into words and bigrams
            words = text_lower.split()
            tokens = words + [" ".join(words[i:i+2]) for i in range(len(words) - 1)]

            found_cards: set[int] = set()
            for token in tokens:
                if len(token) < 3:
                    continue
                match = rfprocess.extractOne(
                    token,
                    alias_keys,
                    scorer=fuzz.token_sort_ratio,
                    score_cutoff=_FUZZY_THRESHOLD,
                )
                if match:
                    card_id = aliases[match[0]]
                    found_cards.add(card_id)

            for card_id in found_cards:
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO signal_card_tags (signal_id, card_id) VALUES (?,?)",
                        (signal_id, card_id),
                    )
                except sqlite3.IntegrityError:
                    pass

            con.execute(
                "UPDATE signals SET tagged_at=? WHERE id=?",
                (now, signal_id),
            )
            if found_cards:
                tagged_count += 1
                all_tagged_card_ids.update(found_cards)

        # Resolve card_ids → card_keys for on-demand price fetching
        newly_tagged_keys: list[str] = []
        if all_tagged_card_ids:
            placeholders = ",".join("?" * len(all_tagged_card_ids))
            rows = con.execute(
                f"SELECT card_key FROM cards WHERE id IN ({placeholders})",
                list(all_tagged_card_ids),
            ).fetchall()
            newly_tagged_keys = [r[0] for r in rows]

        con.commit()
        logger.info("Tagged %d/%d signals with card matches", tagged_count, len(signals))
        return tagged_count, newly_tagged_keys
    finally:
        con.close()


async def job_signal_tagger(db_path: str) -> None:
    """APScheduler job wrapper for signal tagging + on-demand price fetch."""
    import asyncio
    from src.scrapers.futgg import FutGGScraper
    try:
        loop = asyncio.get_running_loop()
        count, newly_tagged_keys = await loop.run_in_executor(None, run_tagging, db_path)
        logger.info("JOB DONE   signal_tagger — %d signals tagged", count)

        if not newly_tagged_keys:
            return

        # Fetch fresh prices for any newly-tagged card that is stale (> 2h old)
        logger.info("signal_tagger: checking price freshness for %d card(s)", len(newly_tagged_keys))
        async with FutGGScraper(db_path=db_path) as scraper:
            for card_key in newly_tagged_keys:
                for platform in ("pc", "console"):
                    await scraper.fetch_card_on_demand(card_key, platform)  # type: ignore[arg-type]
    except Exception as exc:
        logger.error("JOB FAILED signal_tagger — %s: %s", type(exc).__name__, exc)
