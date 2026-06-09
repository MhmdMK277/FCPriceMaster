"""
Discord ingestion worker for FCPriceMaster.
Entry point: uv run python -m src.workers.discord_ingest

Reads forwarded trade-call messages from allowlisted channels on the owner's
server and persists them as signals in SQLite. Bot never joins external servers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import discord
import httpx
import yaml
from dotenv import load_dotenv
from src.llm.providers.nvidia_provider import NvidiaVisionProvider
from src.workers.signal_tagger import run_tagging

_ROOT = Path(__file__).parents[3]
_DB_PATH = str(_ROOT / "data" / "fcpricemaster.db")
_LOG_DIR = _ROOT / "data" / "logs"
_CONFIG_PATH = _ROOT / "config" / "discord_sources.yaml"
_DOTENV_PATH = _ROOT / ".env"
_TARGET_GUILD_ID = 1475125630180917268

logger = logging.getLogger(__name__)

_VISION_PROMPT = """This is a screenshot from a FUT (EA Sports FC) trading Discord server.
Extract the following information if visible in the image:
- Player name
- Card version/type (e.g. TOTS, TOTW, Icon, Hero, Gold)
- Overall rating (number)
- Suggested action (buy/sell/hold if visible)
- Any price mentioned
Return JSON only: {player_name, card_version, rating, action, price, confidence}
If the image is not a FUT card, return {player_name: null}"""


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = RotatingFileHandler(
        _LOG_DIR / "discord_ingest.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def load_config() -> dict[int, dict[str, Any]]:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"discord_sources.yaml not found at {_CONFIG_PATH}")
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    raw = cfg.get("channels", {})
    if not raw:
        raise ValueError("No channels configured in discord_sources.yaml")
    return {int(k): v for k, v in raw.items()}


def load_token() -> str:
    # Suppress dotenv parse warnings: owner's .env uses label-value format (not KEY=VALUE)
    # so blank/label lines are expected to be unparseable — suppress rather than spam logs.
    logging.getLogger("dotenv.main").setLevel(logging.ERROR)
    # Try standard KEY=VALUE dotenv first
    load_dotenv(_DOTENV_PATH, override=True)
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if token:
        return token

    # Fallback: parse owner's label-style .env format.
    # The file has labels on one line and values on the next, e.g.:
    #   Token
    #   MTQ5NTU1NjQ0NTAzNzY2MjM2MQ.G...
    if _DOTENV_PATH.exists():
        lines = _DOTENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower() == "token" and i + 1 < len(lines):
                candidate = lines[i + 1].strip()
                if candidate:
                    return candidate

    raise EnvironmentError(
        "DISCORD_BOT_TOKEN not found.\n"
        "Expected either DISCORD_BOT_TOKEN=<value> in the .env file,\n"
        "or a 'Token' label followed by the token value on the next line.\n"
        f"File checked: {_DOTENV_PATH}"
    )


def open_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ---------------------------------------------------------------------------
# Parsing (pure function, testable without Discord client)
# ---------------------------------------------------------------------------

def parse_message(
    message: Any, channel_config: dict[str, Any]
) -> dict[str, Any]:
    """
    Parse a discord.Message into a dict ready for DB insertion.
    Never raises — fills None for fields that cannot be extracted.
    """
    msg_id = str(message.id)
    ts_utc = message.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    source_label = channel_config.get("source_label", "unknown")

    snapshots = getattr(message, "message_snapshots", []) or []
    if snapshots:
        # discord.py 2.5+: MessageSnapshot exposes content/attachments/created_at
        # directly on the snapshot object — there is no .message sub-attribute.
        snap = snapshots[0]
        text = getattr(snap, "content", None)
        raw_text = text if text else None  # NULL when image-only forward
        orig_created = getattr(snap, "created_at", None)
        original_ts_utc = (
            orig_created.strftime("%Y-%m-%dT%H:%M:%SZ") if orig_created else None
        )
        # MessageSnapshot has no author field; record who forwarded the message instead.
        orig_author = getattr(message, "author", None)
        original_author = str(orig_author) if orig_author else None
        attachments = list(getattr(snap, "attachments", []))
        source_server = source_label
        signal_type = "forward"
    else:
        text = message.content
        raw_text = text if text else None
        original_ts_utc = None
        orig_author = getattr(message, "author", None)
        original_author = str(orig_author) if orig_author else None
        attachments = list(getattr(message, "attachments", []))
        source_server = "owner_direct"
        signal_type = "direct"

    attachment_data = [
        {
            "url": getattr(att, "url", ""),
            "content_type": getattr(att, "content_type", None),
            "width": getattr(att, "width", None),
            "height": getattr(att, "height", None),
        }
        for att in attachments
    ]

    return {
        "message_id": msg_id,
        "ts_utc": ts_utc,
        "signal_type": signal_type,
        "raw_text": raw_text,
        "source_server": source_server,
        "original_author": original_author,
        "original_ts_utc": original_ts_utc,
        "has_attachments": 1 if attachment_data else 0,
        "attachments": attachment_data,
    }


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def persist_signal(
    conn: sqlite3.Connection, parsed: dict[str, Any]
) -> int | None:
    """
    Atomically insert signal, attachments, and dedup record.
    Returns signal_id on success, None if already exists (dedup).
    Rolls back on any other DB error and re-raises.
    """
    msg_id = parsed["message_id"]

    try:
        # BEGIN IMMEDIATE holds the write lock so concurrent coroutines cannot
        # both see an empty dedup table and race to insert the same message.
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            "SELECT signal_id FROM discord_message_ids WHERE message_id = ?", (msg_id,)
        ).fetchone()
        if existing:
            conn.execute("ROLLBACK")
            logger.debug("DEDUP skip message_id=%s (signal_id=%s)", msg_id, existing[0])
            return None

        cur = conn.execute(
            """
            INSERT INTO signals
                (source, source_id, ts_utc, signal_type, raw_text,
                 source_server, original_author, original_ts_utc, has_attachments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "discord",
                msg_id,
                parsed["ts_utc"],
                parsed["signal_type"],
                parsed["raw_text"],
                parsed["source_server"],
                parsed["original_author"],
                parsed["original_ts_utc"],
                parsed["has_attachments"],
            ),
        )
        signal_id = cur.lastrowid

        for att in parsed["attachments"]:
            conn.execute(
                """
                INSERT INTO signal_attachments (signal_id, url, content_type, width, height)
                VALUES (?, ?, ?, ?, ?)
                """,
                (signal_id, att["url"], att["content_type"], att["width"], att["height"]),
            )

        conn.execute(
            "INSERT INTO discord_message_ids (message_id, signal_id) VALUES (?, ?)",
            (msg_id, signal_id),
        )
        conn.execute("COMMIT")
        return signal_id
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK")
        logger.debug("DEDUP (IntegrityError) message_id=%s: %s", msg_id, exc)
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise


async def process_vision_attachment(
    conn: sqlite3.Connection,
    signal_id: int,
    parsed: dict[str, Any],
    db_path: str,
) -> None:
    """Parse at most one Discord image attachment with Mistral Vision."""
    image_att = next(
        (
            att for att in parsed.get("attachments", [])
            if (att.get("content_type") or "").lower() in ("image/png", "image/jpeg", "image/jpg")
            or att.get("url", "").lower().split("?", 1)[0].endswith((".png", ".jpg", ".jpeg"))
        ),
        None,
    )
    if not image_att:
        return

    provider = NvidiaVisionProvider()
    if not provider.is_available():
        logger.warning("NVIDIA_API_KEY not set; skipping Discord image vision for signal_id=%s", signal_id)
        return

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(image_att["url"])
            resp.raise_for_status()
            image_b64 = base64.b64encode(resp.content).decode("ascii")

        raw_json = await provider.extract_json(_VISION_PROMPT, image_b64)
        extracted = json.loads(raw_json)
    except Exception as exc:
        logger.warning("Discord image vision failed for signal_id=%s: %s", signal_id, exc)
        return

    try:
        conn.execute(
            "UPDATE signal_attachments SET vision_extracted=? WHERE signal_id=? AND url=?",
            (raw_json, signal_id, image_att["url"]),
        )
        player_name = extracted.get("player_name")
        if player_name:
            card_version = extracted.get("card_version") or ""
            rating = extracted.get("rating") or ""
            action = extracted.get("action") or ""
            price = extracted.get("price") or ""
            image_note = " ".join(
                str(part).strip()
                for part in (player_name, card_version, rating)
                if str(part or "").strip()
            )
            action_price = " @ ".join(
                str(part).strip()
                for part in (action, price)
                if str(part or "").strip()
            )
            if action_price:
                image_note = f"{image_note} - {action_price}"
            appended = f"[IMAGE: {image_note}]"
            row = conn.execute("SELECT raw_text FROM signals WHERE id=?", (signal_id,)).fetchone()
            current = row[0] if row and row[0] else ""
            new_text = f"{current}\n{appended}".strip() if current else appended
            conn.execute(
                "UPDATE signals SET raw_text=?, tagged_at=NULL WHERE id=?",
                (new_text, signal_id),
            )
            conn.commit()
            run_tagging(db_path, batch_size=10)
        else:
            conn.commit()
    except Exception as exc:
        logger.warning("Failed to persist Discord vision result for signal_id=%s: %s", signal_id, exc)


# ---------------------------------------------------------------------------
# Discord client
# ---------------------------------------------------------------------------

class DiscordIngestClient(discord.Client):
    def __init__(
        self,
        channel_configs: dict[int, dict[str, Any]],
        db_path: str,
        target_guild_id: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.channel_configs = channel_configs
        self.db_path = db_path
        self.target_guild_id = target_guild_id
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = open_db(self.db_path)
        return self._conn

    async def on_ready(self) -> None:
        user_info = f"{self.user} (id={self.user.id})" if self.user else "unknown"
        logger.info(
            "on_ready: bot=%s connected to guild=%s", user_info, self.target_guild_id
        )
        guild = self.get_guild(self.target_guild_id)
        if guild is None:
            logger.warning(
                "Target guild %s not visible — bot may not be a member", self.target_guild_id
            )
            return

        visible = 0
        for ch_id, ch_cfg in self.channel_configs.items():
            ch = guild.get_channel(ch_id)
            if ch is None:
                logger.warning(
                    "Allowlisted channel %s (%s) not visible — "
                    "check bot View Channel + Read Message History permissions",
                    ch_id, ch_cfg.get("source_label"),
                )
            else:
                visible += 1
                logger.info(
                    "  channel OK: #%s (id=%s) label=%s",
                    getattr(ch, "name", ch_id), ch_id, ch_cfg.get("source_label"),
                )

        logger.info("%d / %d allowlisted channels visible", visible, len(self.channel_configs))
        await self._backfill(guild)

    async def _backfill(self, guild: discord.Guild) -> None:
        logger.info("Backfill: fetching last 100 messages per allowlisted channel...")
        conn = self._get_conn()
        total = 0
        for ch_id, ch_cfg in self.channel_configs.items():
            ch = guild.get_channel(ch_id)
            if ch is None or not isinstance(ch, discord.TextChannel):
                continue
            try:
                async for msg in ch.history(limit=100, oldest_first=False):
                    if self.user and msg.author.id == self.user.id:
                        continue
                    try:
                        parsed = parse_message(msg, ch_cfg)
                        result = persist_signal(conn, parsed)
                        if result is not None:
                            await process_vision_attachment(conn, result, parsed, self.db_path)
                            total += 1
                    except Exception as exc:
                        logger.warning(
                            "Backfill error message_id=%s: %s", msg.id, exc
                        )
            except Exception as exc:
                logger.warning("Backfill channel %s failed: %s", ch_id, exc)

        logger.info("Backfill complete — %d new signals ingested", total)

    async def on_message(self, message: discord.Message) -> None:
        ch_id = message.channel.id

        if ch_id not in self.channel_configs:
            return

        if self.user and message.author.id == self.user.id:
            return

        if message.guild is None or message.guild.id != self.target_guild_id:
            return

        ch_cfg = self.channel_configs[ch_id]
        try:
            parsed = parse_message(message, ch_cfg)
            conn = self._get_conn()
            signal_id = persist_signal(conn, parsed)
            if signal_id is not None:
                await process_vision_attachment(conn, signal_id, parsed, self.db_path)
                logger.info(
                    "SIGNAL ingested id=%s type=%s source=%s author=%s text=%.80r",
                    signal_id, parsed["signal_type"], parsed["source_server"],
                    parsed["original_author"], parsed["raw_text"],
                )
        except Exception as exc:
            logger.error(
                "Error processing message_id=%s: %s: %s",
                message.id, type(exc).__name__, exc,
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run(db_path: str = _DB_PATH) -> None:
    setup_logging()
    logger.info(
        "FCPriceMaster Discord ingest worker starting (pid=%d)", os.getpid()
    )

    token = load_token()
    channel_configs = load_config()
    logger.info(
        "Configured channels: %s",
        {k: v.get("source_label") for k, v in channel_configs.items()},
    )

    intents = discord.Intents.none()
    intents.guilds = True          # required: populates guild/channel cache
    intents.guild_messages = True
    intents.message_content = True

    client = DiscordIngestClient(
        channel_configs=channel_configs,
        db_path=db_path,
        target_guild_id=_TARGET_GUILD_ID,
        intents=intents,
    )

    stop_event = asyncio.Event()

    def _on_signal(*_: Any) -> None:
        logger.info("Shutdown signal received — closing Discord connection...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, OSError):
            signal.signal(sig, lambda _s, _f: _on_signal())

    bot_task = loop.create_task(client.start(token))

    await stop_event.wait()

    await client.close()
    try:
        await asyncio.wait_for(bot_task, timeout=10.0)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass

    if client._conn is not None:
        client._conn.close()

    logger.info("Discord ingest worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run())
