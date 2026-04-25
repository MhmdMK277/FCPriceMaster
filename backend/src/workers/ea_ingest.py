"""
EA FC news ingestion job for FCPriceMaster.

Polls EA's news page every 30 minutes via the scheduler.
Tries RSS first; falls back to HTML scraping with httpx + selectolax.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import httpx
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

_RSS_URLS = [
    "https://www.ea.com/games/ea-sports-fc/news/rss",
    "https://www.ea.com/en-gb/games/ea-sports-fc/news/rss",
]
_HTML_URL = "https://www.ea.com/games/ea-sports-fc/news"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_TEXT_CHARS = 2000


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_health(
    db_path: str, success: bool, records: int = 0, error: str | None = None
) -> None:
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT consecutive_failures FROM scraper_health WHERE source='ea_news' "
            "ORDER BY run_at_utc DESC LIMIT 1"
        ).fetchone()
        prev = row[0] if row else 0
        consecutive = 0 if success else prev + 1
        con.execute(
            "INSERT INTO scraper_health (source, run_at_utc, success, records_written, "
            "consecutive_failures, last_error) VALUES ('ea_news', ?, ?, ?, ?, ?)",
            (_now_utc(), 1 if success else 0, records, consecutive, error),
        )
        con.commit()
        con.close()
    except Exception as exc:
        logger.error("Failed to write ea_news scraper_health: %s", exc)


def _article_id(url: str) -> str:
    """Stable dedup ID from article URL (or hash of title if no URL)."""
    return hashlib.sha1(url.encode()).hexdigest()[:24]


def _persist_article(
    conn: sqlite3.Connection,
    article_id: str,
    title: str,
    summary: str | None,
    url: str,
    pub_date: str | None,
) -> int | None:
    """Insert EA news article as a signal. Returns signal_id or None (dedup)."""
    try:
        conn.execute("BEGIN IMMEDIATE")

        # Dedup via source_id UNIQUE constraint in signals
        existing = conn.execute(
            "SELECT id FROM signals WHERE source='ea_news' AND source_id=?", (article_id,)
        ).fetchone()
        if existing:
            conn.execute("ROLLBACK")
            return None

        raw = title
        if summary:
            raw += "\n\n" + summary
        raw = raw[:_MAX_TEXT_CHARS]

        ts = pub_date or _now_utc()

        cur = conn.execute(
            """
            INSERT INTO signals
                (source, source_id, ts_utc, signal_type, raw_text,
                 source_server, has_attachments, signal_category, priority)
            VALUES ('ea_news', ?, ?, 'article', ?, 'ea_official', 0, 'news', 'high')
            """,
            (article_id, ts, raw),
        )
        signal_id = cur.lastrowid
        conn.execute("COMMIT")
        return signal_id
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _parse_rss_date(date_str: str | None) -> str | None:
    """Parse RSS pubDate like 'Mon, 01 Jan 2024 12:00:00 +0000' to our UTC format."""
    if not date_str:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


async def _try_rss(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Attempt to fetch and parse EA's news RSS. Returns list of article dicts."""
    for rss_url in _RSS_URLS:
        try:
            resp = await client.get(rss_url)
            if resp.status_code != 200:
                continue
            ctype = resp.headers.get("content-type", "")
            if "xml" not in ctype and "rss" not in ctype and not resp.text.strip().startswith("<"):
                continue
            root = ElementTree.fromstring(resp.text)
            items = root.findall(".//item")
            if not items:
                continue
            results = []
            for item in items:
                title = (item.findtext("title") or "").strip()
                url = (item.findtext("link") or item.findtext("guid") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub = _parse_rss_date(item.findtext("pubDate"))
                if title and url:
                    results.append({"title": title, "url": url, "desc": desc, "pub": pub})
            if results:
                logger.info("EA news: fetched %d items via RSS from %s", len(results), rss_url)
                return results
        except Exception as exc:
            logger.debug("RSS attempt failed for %s: %s", rss_url, exc)
    return []


async def _try_html_scrape(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Scrape EA FC news HTML page. Selectolax parses server-rendered HTML."""
    try:
        resp = await client.get(_HTML_URL)
        resp.raise_for_status()
        tree = HTMLParser(resp.text)
        results = []

        # EA's news page uses article cards; look for <a> with a heading inside.
        # Selectors may drift — schema-guard warning logged if nothing found.
        for a_tag in tree.css("a[href]"):
            href = a_tag.attributes.get("href", "")
            if "/news/" not in href:
                continue
            # Build full URL if relative
            if href.startswith("/"):
                href = "https://www.ea.com" + href
            heading = a_tag.css_first("h1, h2, h3, h4")
            if not heading:
                continue
            title = heading.text(strip=True)
            if not title or len(title) < 10:
                continue
            # Avoid duplicates from repeated links on the page
            if any(r["url"] == href for r in results):
                continue
            results.append({"title": title, "url": href, "desc": None, "pub": None})

        if not results:
            logger.warning(
                "EA news HTML scrape: no articles found — page structure may have changed"
            )
        else:
            logger.info("EA news: scraped %d articles from HTML", len(results))
        return results
    except Exception as exc:
        logger.error("EA news HTML scrape error: %s", exc)
        return []


async def job_ea_news(db_path: str) -> None:
    """Fetch EA FC news. Runs every 30 min via scheduler."""
    logger.info("JOB START  ea_news")
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json, application/xml, text/html, */*"}
    total = 0
    error: str | None = None

    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers=headers
        ) as client:
            articles = await _try_rss(client)
            if not articles:
                articles = await _try_html_scrape(client)

        if not articles:
            error = "No articles found via RSS or HTML scrape"
            _write_health(db_path, success=False, error=error)
            logger.warning("JOB DONE   ea_news — %s", error)
            return

        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            for art in articles:
                aid = _article_id(art["url"])
                title = art["title"]
                if _persist_article(conn, aid, title, art.get("desc"), art["url"], art.get("pub")) is not None:
                    total += 1
        finally:
            conn.close()

        _write_health(db_path, success=True, records=total)
        logger.info("JOB DONE   ea_news — %d new articles ingested", total)

    except Exception as exc:
        logger.error("JOB FAILED ea_news — %s: %s", type(exc).__name__, exc)
        _write_health(db_path, success=False, error=str(exc))
