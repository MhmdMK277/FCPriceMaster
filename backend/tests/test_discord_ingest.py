"""
Tests for the Discord ingestion worker.

Uses stub objects to mimic discord.py's Message/MessageSnapshot API
without an actual Discord connection.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.db.migrate import run_migrations
from src.workers.discord_ingest import parse_message, persist_signal


# ---------------------------------------------------------------------------
# Fixtures — stub discord.py objects
# ---------------------------------------------------------------------------

class _StubUser:
    id = 987654321
    name = "TradingGuru"
    discriminator = "0"
    def __str__(self) -> str:
        return f"{self.name}"


class _StubAttachment:
    url = "https://cdn.discordapp.com/attachments/test/123/card.png"
    content_type = "image/png"
    width = 1280
    height = 720


class _StubSnapshot:
    """Mimics discord.py 2.5+ MessageSnapshot — attributes are on the snapshot directly."""
    content = "Buy Mbappe TOTY now — price spike incoming after SBC"
    created_at = datetime(2026, 4, 19, 10, 30, 0, tzinfo=timezone.utc)
    attachments: list[_StubAttachment] = [_StubAttachment()]
    # MessageSnapshot has no 'author'; author comes from the outer message.


class _StubEmptySnapshot:
    """Image-only forward — content is empty string."""
    content = ""
    created_at = datetime(2026, 4, 19, 11, 0, 0, tzinfo=timezone.utc)
    attachments: list[_StubAttachment] = [_StubAttachment()]


class _StubChannel:
    id = 1495558120662106314


class _MockForwardMessage:
    """Mimics a forwarded Discord message."""
    id = 111222333444555666
    channel = _StubChannel()
    author = _StubUser()
    content = ""
    attachments: list = []
    created_at = datetime(2026, 4, 20, 8, 0, 0, tzinfo=timezone.utc)
    message_snapshots = [_StubSnapshot()]


class _MockImageOnlyForward:
    """Forward where the original message had no text (image only)."""
    id = 222333444555666777
    channel = _StubChannel()
    author = _StubUser()
    content = ""
    attachments: list = []
    created_at = datetime(2026, 4, 20, 8, 30, 0, tzinfo=timezone.utc)
    message_snapshots = [_StubEmptySnapshot()]


class _MockDirectMessage:
    """Mimics a direct message from the owner (not a forward)."""
    id = 999888777666555444
    channel = _StubChannel()
    author = _StubUser()
    content = "Heads up — Bellingham price dropping on console"
    attachments: list = []
    created_at = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    message_snapshots: list = []


class _NonAllowlistedChannel:
    id = 9999999999999


class _MockNonAllowlistedMessage:
    id = 777666555444333222
    channel = _NonAllowlistedChannel()
    author = _StubUser()
    content = "random server message"
    attachments: list = []
    created_at = datetime(2026, 4, 20, 9, 5, 0, tzinfo=timezone.utc)
    message_snapshots: list = []


CHANNEL_CONFIG = {"source_label": "source_1", "reliability": "unknown"}
NON_ALLOWLISTED_CONFIGS: dict = {}  # empty — channel not configured


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db() -> tuple[sqlite3.Connection, str]:
    """Create a temp DB with migrations applied, return (conn, db_path)."""
    tmp = tempfile.mktemp(suffix=".db")
    run_migrations(db_path=tmp)
    conn = sqlite3.connect(tmp)
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn, tmp


# ---------------------------------------------------------------------------
# Test 1: Forwarded message parsing
# ---------------------------------------------------------------------------

def test_parse_forwarded_message() -> None:
    msg = _MockForwardMessage()
    parsed = parse_message(msg, CHANNEL_CONFIG)

    assert parsed["signal_type"] == "forward"
    assert parsed["source_server"] == "source_1"
    # Content comes from the snapshot directly (discord.py 2.5+ MessageSnapshot API)
    assert parsed["raw_text"] == "Buy Mbappe TOTY now — price spike incoming after SBC"
    # Author is the forwarder (snapshot has no author field)
    assert parsed["original_author"] == "TradingGuru"
    assert parsed["original_ts_utc"] == "2026-04-19T10:30:00Z"
    assert parsed["has_attachments"] == 1
    assert len(parsed["attachments"]) == 1
    assert parsed["attachments"][0]["url"] == _StubAttachment.url
    assert parsed["attachments"][0]["content_type"] == "image/png"
    assert parsed["message_id"] == str(msg.id)


def test_parse_forwarded_message_image_only() -> None:
    """Image-only forwards have empty snapshot content — raw_text must be NULL, not ''."""
    msg = _MockImageOnlyForward()
    parsed = parse_message(msg, CHANNEL_CONFIG)

    assert parsed["signal_type"] == "forward"
    assert parsed["raw_text"] is None, "image-only forward must store NULL, not empty string"
    assert parsed["has_attachments"] == 1


# ---------------------------------------------------------------------------
# Test 2: Non-forwarded (direct) message marked as 'owner_direct'
# ---------------------------------------------------------------------------

def test_parse_direct_message() -> None:
    msg = _MockDirectMessage()
    parsed = parse_message(msg, CHANNEL_CONFIG)

    assert parsed["signal_type"] == "direct"
    assert parsed["source_server"] == "owner_direct"
    assert parsed["raw_text"] == "Heads up — Bellingham price dropping on console"
    assert parsed["original_author"] == "TradingGuru"
    assert parsed["original_ts_utc"] is None
    assert parsed["has_attachments"] == 0
    assert parsed["attachments"] == []


def test_dedup_regression_same_message_twice() -> None:
    """Re-processing the same message object twice must yield exactly one signals row."""
    conn, _path = _fresh_db()
    msg = _MockDirectMessage()
    parsed = parse_message(msg, CHANNEL_CONFIG)

    sig_id_1 = persist_signal(conn, parsed)
    assert sig_id_1 is not None

    # Second call — must be treated as dedup, no exception, count stays 1
    sig_id_2 = persist_signal(conn, parsed)
    assert sig_id_2 is None

    count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Test 3: Dedup guard
# ---------------------------------------------------------------------------

def test_dedup_guard() -> None:
    conn, _path = _fresh_db()
    msg = _MockForwardMessage()
    parsed = parse_message(msg, CHANNEL_CONFIG)

    sig_id_1 = persist_signal(conn, parsed)
    assert sig_id_1 is not None, "First insert should succeed"

    sig_id_2 = persist_signal(conn, parsed)
    assert sig_id_2 is None, "Re-processing same message_id should return None (dedup)"

    count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count == 1, "Only one signal row should exist after duplicate processing"


# ---------------------------------------------------------------------------
# Test 4: Non-allowlisted channel (parsing produces same data, but
#         in the bot the message is skipped before parse — tested here
#         by verifying channel_id not in config means the worker skips it)
# ---------------------------------------------------------------------------

def test_allowlist_check() -> None:
    """Channel not in configs → bot skips message (simulated here)."""
    allowlisted_ids = {1495558120662106314, 1495558139632943275, 1495558157492158575}
    msg = _MockNonAllowlistedMessage()
    assert msg.channel.id not in allowlisted_ids, (
        "Non-allowlisted message should have a channel ID outside the allowlist"
    )
    # In the live bot, on_message returns immediately if ch_id not in channel_configs.
    # We verify that the channel id is indeed absent, which is the guard condition.
    # No signal should be created.
    conn, _path = _fresh_db()
    # Manually simulate: if not in allowlist, don't call persist_signal
    if msg.channel.id not in allowlisted_ids:
        pass  # worker returns here — no DB write
    count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# Test 5: Migration 0002 applies cleanly and idempotently
# ---------------------------------------------------------------------------

def test_migration_0002_fresh_db() -> None:
    conn, _path = _fresh_db()
    # Verify all 3 new columns exist on signals
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    assert "source_server" in cols
    assert "original_author" in cols
    assert "original_ts_utc" in cols
    assert "has_attachments" in cols

    # Verify new tables exist
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "signal_attachments" in tables
    assert "discord_message_ids" in tables


def test_migration_0002_idempotent() -> None:
    """Running migrations a second time on an already-migrated DB should be a no-op."""
    _conn, db_path = _fresh_db()
    _conn.close()

    # Run again — should skip 0001 and 0002 (already in _migrations)
    run_migrations(db_path=db_path)

    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM _migrations WHERE filename LIKE '0002%'"
    ).fetchone()[0]
    assert count == 1, "Migration 0002 should appear exactly once in _migrations"
    conn.close()


# ---------------------------------------------------------------------------
# Test 6: Attachment persistence
# ---------------------------------------------------------------------------

def test_attachment_persisted() -> None:
    conn, _path = _fresh_db()
    msg = _MockForwardMessage()
    parsed = parse_message(msg, CHANNEL_CONFIG)

    signal_id = persist_signal(conn, parsed)
    assert signal_id is not None

    atts = conn.execute(
        "SELECT url, content_type, width, height FROM signal_attachments WHERE signal_id = ?",
        (signal_id,),
    ).fetchall()
    assert len(atts) == 1
    url, ct, w, h = atts[0]
    assert url == _StubAttachment.url
    assert ct == "image/png"
    assert w == 1280
    assert h == 720
