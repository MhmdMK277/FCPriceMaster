"""Tests for Twitter ingest worker: cookie loader, tweet parsing, DB persistence."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.utils.cookie_loader import load_netscape_cookies
from src.workers.twitter_ingest import (
    generate_tweet_id,
    parse_tweet_id_from_href,
    parse_tweet_data,
    persist_tweet,
    open_db,
)
from src.db.migrate import run_migrations


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    run_migrations(path)
    yield path
    try:
        os.unlink(path)
        for suf in ("-wal", "-shm"):
            p = path + suf
            if os.path.exists(p):
                os.unlink(p)
    except OSError:
        pass


@pytest.fixture()
def cookie_file():
    with tempfile.NamedTemporaryFile(
        suffix=".txt", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(
            "# Netscape HTTP Cookie File\n"
            ".x.com\tTRUE\t/\tTRUE\t9999999999\tauth_token\tabc123\n"
            ".x.com\tTRUE\t/\tFALSE\t9999999999\tct0\txyz789\n"
            ".x.com\tTRUE\t/\tFALSE\t9999999999\tother\tval\n"
        )
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# cookie_loader tests
# ---------------------------------------------------------------------------

def test_cookie_loader_valid(cookie_file: str) -> None:
    cookies = load_netscape_cookies(cookie_file)
    names = {c["name"] for c in cookies}
    assert "auth_token" in names
    assert "ct0" in names
    assert len(cookies) == 3


def test_cookie_loader_missing_session_cookies_raises() -> None:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tFALSE\t0\tguest_id\tgabc\n")
        path = f.name
    try:
        with pytest.raises(ValueError, match="auth_token"):
            load_netscape_cookies(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_cookie_loader_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_netscape_cookies("/nonexistent/path/cookies.txt")


def test_cookie_loader_secure_flag(cookie_file: str) -> None:
    cookies = load_netscape_cookies(cookie_file)
    auth = next(c for c in cookies if c["name"] == "auth_token")
    ct0  = next(c for c in cookies if c["name"] == "ct0")
    assert auth["secure"] is True
    assert ct0["secure"] is False


# ---------------------------------------------------------------------------
# Tweet parsing tests
# ---------------------------------------------------------------------------

def test_parse_tweet_id_from_href_valid() -> None:
    href = "/FutSheriff/status/1782345678901234567"
    assert parse_tweet_id_from_href(href) == "1782345678901234567"


def test_parse_tweet_id_from_href_no_status() -> None:
    assert parse_tweet_id_from_href("/some/other/link") is None
    assert parse_tweet_id_from_href("") is None


def test_generate_tweet_id_stable() -> None:
    id1 = generate_tweet_id("FutSheriff", "2024-04-20T10:00:00Z")
    id2 = generate_tweet_id("FutSheriff", "2024-04-20T10:00:00Z")
    assert id1 == id2
    assert id1.startswith("fallback_")


def test_parse_tweet_data_with_config() -> None:
    account_config = {
        "futsheriff": {"category": "leaks", "priority": "high"},
    }
    raw = {
        "handle": "FutSheriff",
        "text": "New TOTS leaked!",
        "timestamp": "2024-04-20T10:00:00Z",
        "tweet_id": "1782345678901234567",
        "media_urls": [],
    }
    data = parse_tweet_data(raw, account_config)
    assert data["tweet_id"] == "1782345678901234567"
    assert data["signal_category"] == "leaks"
    assert data["priority"] == "high"
    assert data["handle"] == "FutSheriff"
    assert data["has_attachments"] == 0


def test_parse_tweet_data_unknown_handle_defaults() -> None:
    data = parse_tweet_data({"handle": "UnknownAccount", "text": "test"}, {})
    assert data["signal_category"] == "discussion"
    assert data["priority"] == "medium"


def test_parse_tweet_data_with_media() -> None:
    raw = {
        "handle": "FUTDonkey",
        "text": "Check this card",
        "timestamp": "2024-04-20T10:00:00Z",
        "media_urls": ["https://pbs.twimg.com/media/abc.jpg"],
    }
    data = parse_tweet_data(raw, {})
    assert data["has_attachments"] == 1
    assert len(data["media_urls"]) == 1


# ---------------------------------------------------------------------------
# DB persistence tests
# ---------------------------------------------------------------------------

def test_persist_tweet_inserts(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    data = {
        "tweet_id": "1111111111",
        "handle": "FutSheriff",
        "ts_utc": "2024-04-20T10:00:00Z",
        "raw_text": "TOTS announced!",
        "signal_category": "leaks",
        "priority": "high",
        "has_attachments": 0,
        "media_urls": [],
    }
    signal_id = persist_tweet(conn, data)
    assert signal_id is not None

    row = conn.execute("SELECT source, source_server, priority FROM signals WHERE id=?", (signal_id,)).fetchone()
    assert row[0] == "twitter"
    assert row[1] == "FutSheriff"
    assert row[2] == "high"

    dedup = conn.execute("SELECT signal_id FROM twitter_tweet_ids WHERE tweet_id='1111111111'").fetchone()
    assert dedup is not None
    conn.close()


def test_persist_tweet_dedup(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    data = {
        "tweet_id": "2222222222",
        "handle": "FutSheriff",
        "ts_utc": "2024-04-20T10:00:00Z",
        "raw_text": "Duplicate tweet",
        "signal_category": "leaks",
        "priority": "medium",
        "has_attachments": 0,
        "media_urls": [],
    }
    id1 = persist_tweet(conn, data)
    id2 = persist_tweet(conn, data)
    assert id1 is not None
    assert id2 is None
    count = conn.execute("SELECT COUNT(*) FROM signals WHERE source='twitter'").fetchone()[0]
    assert count == 1
    conn.close()


def test_persist_tweet_with_media(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    data = {
        "tweet_id": "3333333333",
        "handle": "FUTDonkey",
        "ts_utc": "2024-04-20T10:00:00Z",
        "raw_text": "Image tweet",
        "signal_category": "leaks",
        "priority": "medium",
        "has_attachments": 1,
        "media_urls": ["https://pbs.twimg.com/media/abc.jpg"],
    }
    signal_id = persist_tweet(conn, data)
    assert signal_id is not None
    att = conn.execute("SELECT url FROM signal_attachments WHERE signal_id=?", (signal_id,)).fetchone()
    assert att is not None
    assert "pbs.twimg.com" in att[0]
    conn.close()
