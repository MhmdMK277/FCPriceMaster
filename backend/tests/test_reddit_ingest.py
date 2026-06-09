"""Tests for Reddit ingestion: flair classification, post persistence, dedup."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workers.reddit_ingest import (
    _classify_flair,
    _fetch_subreddit_posts,
    _post_id,
    _persist_post,
)
from src.db.migrate import run_migrations
from src.workers.twitter_ingest import open_db


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


# ---------------------------------------------------------------------------
# Flair classification
# ---------------------------------------------------------------------------

def test_classify_trading_flairs() -> None:
    assert _classify_flair("Trading") == "trading"
    assert _classify_flair("PRICE CHECK") == "trading"
    assert _classify_flair("market update") == "trading"


def test_classify_news_flairs() -> None:
    assert _classify_flair("News") == "news"
    assert _classify_flair("Patch Notes") == "news"
    assert _classify_flair("EA Announcement") == "news"


def test_classify_meta_flairs() -> None:
    assert _classify_flair("Meta") == "meta"
    assert _classify_flair("Strategy Guide") == "meta"


def test_classify_none_flair() -> None:
    assert _classify_flair(None) == "discussion"
    assert _classify_flair("") == "discussion"
    assert _classify_flair("Random post") == "discussion"


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def _make_post(post_id: str = "abc123", title: str = "Test post") -> dict:
    return {
        "id": post_id,
        "title": title,
        "selftext": "Some body text",
        "created_utc": 1713600000.0,
        "link_flair_text": "Trading",
    }


def test_persist_post_inserts(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    post = _make_post()
    composite = _post_id("fut", "abc123")
    signal_id = _persist_post(conn, composite, "fut", post, priority="medium")
    assert signal_id is not None

    row = conn.execute("SELECT source, source_server, signal_category FROM signals WHERE id=?", (signal_id,)).fetchone()
    assert row[0] == "reddit"
    assert row[1] == "fut"
    assert row[2] == "trading"

    dedup = conn.execute("SELECT signal_id FROM reddit_post_ids WHERE post_id=?", (composite,)).fetchone()
    assert dedup is not None
    conn.close()


def test_persist_post_dedup(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    post = _make_post(post_id="dup123")
    composite = _post_id("fut", "dup123")
    id1 = _persist_post(conn, composite, "fut", post)
    id2 = _persist_post(conn, composite, "fut", post)
    assert id1 is not None
    assert id2 is None
    conn.close()


def test_persist_post_removed_selftext(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    post = {**_make_post(post_id="rem123"), "selftext": "[removed]"}
    composite = _post_id("fut", "rem123")
    signal_id = _persist_post(conn, composite, "fut", post)
    assert signal_id is not None
    row = conn.execute("SELECT raw_text FROM signals WHERE id=?", (signal_id,)).fetchone()
    assert "[removed]" not in (row[0] or "")
    conn.close()


def test_persist_post_hot_priority(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    post = _make_post(post_id="hot123")
    composite = _post_id("EASportsFC", "hot123")
    signal_id = _persist_post(conn, composite, "EASportsFC", post, priority="high")
    assert signal_id is not None
    row = conn.execute("SELECT priority FROM signals WHERE id=?", (signal_id,)).fetchone()
    assert row[0] == "high"
    conn.close()


# ---------------------------------------------------------------------------
# httpx mock: JSON parsing and signal insertion
# ---------------------------------------------------------------------------

_SAMPLE_REDDIT_JSON = {
    "kind": "Listing",
    "data": {
        "children": [
            {
                "kind": "t3",
                "data": {
                    "id": "xyz789",
                    "title": "Price check on R9",
                    "selftext": "Worth buying on PC?",
                    "created_utc": 1713600000.0,
                    "link_flair_text": "Trading",
                    "author": "testuser",
                    "permalink": "/r/fut/comments/xyz789/",
                    "score": 42,
                },
            },
            {
                "kind": "t3",
                "data": {
                    "id": "abc000",
                    "title": "EA just dropped TOTW",
                    "selftext": "",
                    "created_utc": 1713600100.0,
                    "link_flair_text": "News",
                    "author": "another",
                    "permalink": "/r/fut/comments/abc000/",
                    "score": 10,
                },
            },
        ]
    },
}


@pytest.mark.asyncio
async def test_fetch_subreddit_posts_parses_json() -> None:
    """Mock httpx response; assert correct post dicts extracted."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _SAMPLE_REDDIT_JSON
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.workers.reddit_ingest.httpx.AsyncClient", return_value=mock_client):
        posts = await _fetch_subreddit_posts("fut", "new", 25, token=None)

    assert len(posts) == 2
    assert posts[0]["id"] == "xyz789"
    assert posts[0]["title"] == "Price check on R9"
    assert posts[1]["link_flair_text"] == "News"


@pytest.mark.asyncio
async def test_fetch_subreddit_posts_inserts_signals(tmp_db: str) -> None:
    """End-to-end: mock httpx, parse response, assert signals written to DB."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _SAMPLE_REDDIT_JSON
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.workers.reddit_ingest.httpx.AsyncClient", return_value=mock_client):
        posts = await _fetch_subreddit_posts("fut", "new", 25, token=None)

    conn = open_db(tmp_db)
    inserted = 0
    for post in posts:
        composite = _post_id("fut", post["id"])
        sig_id = _persist_post(conn, composite, "fut", post, priority="medium")
        if sig_id is not None:
            inserted += 1

    assert inserted == 2
    rows = conn.execute(
        "SELECT source, source_server, signal_category, raw_text FROM signals ORDER BY id"
    ).fetchall()
    assert rows[0][0] == "reddit"
    assert rows[0][1] == "fut"
    assert rows[0][2] == "trading"
    assert "Price check on R9" in rows[0][3]
    assert rows[1][2] == "news"
    conn.close()
