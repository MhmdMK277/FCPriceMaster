"""Tests for EA news ingestion: article dedup, persistence, RSS date parsing."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.workers.ea_ingest import _article_id, _parse_rss_date, _persist_article
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


def test_article_id_is_stable() -> None:
    url = "https://www.ea.com/games/ea-sports-fc/news/tots-announced"
    assert _article_id(url) == _article_id(url)
    assert len(_article_id(url)) == 24


def test_parse_rss_date_valid() -> None:
    result = _parse_rss_date("Mon, 20 Apr 2026 10:00:00 +0000")
    assert result == "2026-04-20T10:00:00Z"


def test_parse_rss_date_gmt() -> None:
    result = _parse_rss_date("Mon, 20 Apr 2026 10:00:00 GMT")
    assert result is not None
    assert "2026-04-20" in result


def test_parse_rss_date_none() -> None:
    assert _parse_rss_date(None) is None
    assert _parse_rss_date("invalid-date") is None


def test_persist_article_inserts(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    url = "https://www.ea.com/news/tots-2024"
    aid = _article_id(url)
    signal_id = _persist_article(conn, aid, "TOTS 2024 Announced", "Full TOTS revealed.", url, "2024-04-20T10:00:00Z")
    assert signal_id is not None

    row = conn.execute("SELECT source, signal_category, priority, source_server FROM signals WHERE id=?", (signal_id,)).fetchone()
    assert row[0] == "ea_news"
    assert row[1] == "news"
    assert row[2] == "high"
    assert row[3] == "ea_official"
    conn.close()


def test_persist_article_dedup(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    url = "https://www.ea.com/news/dedup-test"
    aid = _article_id(url)
    id1 = _persist_article(conn, aid, "Article Title", None, url, None)
    id2 = _persist_article(conn, aid, "Article Title", None, url, None)
    assert id1 is not None
    assert id2 is None
    conn.close()


def test_persist_article_no_summary(tmp_db: str) -> None:
    conn = open_db(tmp_db)
    url = "https://www.ea.com/news/title-only"
    aid = _article_id(url)
    signal_id = _persist_article(conn, aid, "Title Only", None, url, None)
    assert signal_id is not None
    row = conn.execute("SELECT raw_text FROM signals WHERE id=?", (signal_id,)).fetchone()
    assert row[0] == "Title Only"
    conn.close()
