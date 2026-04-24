"""
One-time script: re-fetch and re-parse Discord signals that have empty raw_text.

Run from project root:
    uv run python scripts/reprocess_discord_signals.py

The script:
1. Finds signal rows where source='discord' and (raw_text IS NULL OR raw_text='')
2. For each, tries channel.fetch_message() across all configured channels
3. Re-parses with the fixed parse_message() and UPDATEs the row in place
4. Prints a summary of rows corrected.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from src.workers.discord_ingest import (  # noqa: E402
    load_config,
    load_token,
    parse_message,
)
import discord  # noqa: E402

_DB_PATH = str(ROOT / "data" / "fcpricemaster.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_stale_signals(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Return (signal_id, source_id) rows with empty/null raw_text."""
    return conn.execute(
        "SELECT id, source_id FROM signals WHERE source='discord' AND (raw_text IS NULL OR raw_text='')"
    ).fetchall()


def _update_signal(conn: sqlite3.Connection, signal_id: int, parsed: dict) -> None:
    conn.execute(
        """UPDATE signals
           SET raw_text=?, original_author=?, original_ts_utc=?, has_attachments=?
           WHERE id=?""",
        (
            parsed["raw_text"],
            parsed["original_author"],
            parsed["original_ts_utc"],
            parsed["has_attachments"],
            signal_id,
        ),
    )
    conn.commit()


async def reprocess() -> None:
    conn = sqlite3.connect(_DB_PATH)
    stale = _get_stale_signals(conn)
    if not stale:
        logger.info("No stale signals found — nothing to reprocess.")
        conn.close()
        return

    logger.info("Found %d stale signal(s): %s", len(stale), [r[1] for r in stale])

    token = load_token()
    channel_configs = load_config()

    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True

    client = discord.Client(intents=intents)
    corrected = 0

    @client.event
    async def on_ready() -> None:
        nonlocal corrected
        logger.info("Bot ready as %s — starting reprocess", client.user)

        for signal_id, message_id_str in stale:
            message_id = int(message_id_str)
            fetched = False
            for ch_id, ch_cfg in channel_configs.items():
                ch = client.get_channel(ch_id)
                if ch is None or not isinstance(ch, discord.TextChannel):
                    continue
                try:
                    msg = await ch.fetch_message(message_id)
                    parsed = parse_message(msg, ch_cfg)
                    _update_signal(conn, signal_id, parsed)
                    logger.info(
                        "  signal_id=%d reprocessed — raw_text=%.80r author=%r",
                        signal_id, parsed["raw_text"], parsed["original_author"],
                    )
                    corrected += 1
                    fetched = True
                    break
                except discord.NotFound:
                    continue
                except Exception as exc:
                    logger.warning("  channel %s fetch error: %s", ch_id, exc)

            if not fetched:
                logger.warning("  signal_id=%d message_id=%s not found in any channel", signal_id, message_id_str)

        logger.info("Reprocess complete — %d/%d signals corrected", corrected, len(stale))

        # Print final DB state
        rows = conn.execute(
            "SELECT id, source_id, raw_text, original_author, original_ts_utc FROM signals WHERE source='discord'"
        ).fetchall()
        print("\n--- Final signals state ---")
        for r in rows:
            print(f"  id={r[0]} source_id={r[1]!r} raw_text={r[2]!r} author={r[3]!r} orig_ts={r[4]!r}")

        conn.close()
        await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(reprocess())
