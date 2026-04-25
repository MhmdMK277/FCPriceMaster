"""
Reddit ingestion job functions for FCPriceMaster.

Uses Reddit's public JSON API (no credentials needed — append .json to any
subreddit URL). Called from the APScheduler in scheduler.py.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BASE = "https://old.reddit.com"
_SUBREDDITS = ["fut", "EASportsFC", "fut_economy"]
_NEW_LIMIT = 20
_HOT_LIMIT = 10
_MAX_TEXT_CHARS = 5000


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_from_epoch(epoch: float | int | None) -> str:
    if not epoch:
        return _now_utc()
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, OSError, OverflowError):
        return _now_utc()


def _post_id(subreddit: str, post_id: str) -> str:
    return f"{subreddit}:{post_id}"


def _classify_flair(flair: str | None) -> str:
    if not flair:
        return "discussion"
    fl = flair.lower()
    if any(k in fl for k in ("trading", "trade", "economy", "market", "price")):
        return "trading"
    if any(k in fl for k in ("news", "update", "patch", "announcement")):
        return "news"
    if any(k in fl for k in ("meta", "strategy", "guide")):
        return "meta"
    return "discussion"


def _write_health(
    db_path: str, success: bool, records: int = 0, error: str | None = None
) -> None:
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT consecutive_failures FROM scraper_health WHERE source='reddit' "
            "ORDER BY run_at_utc DESC LIMIT 1"
        ).fetchone()
        prev = row[0] if row else 0
        consecutive = 0 if success else prev + 1
        con.execute(
            "INSERT INTO scraper_health (source, run_at_utc, success, records_written, "
            "consecutive_failures, last_error) VALUES ('reddit', ?, ?, ?, ?, ?)",
            (_now_utc(), 1 if success else 0, records, consecutive, error),
        )
        con.commit()
        con.close()
    except Exception as exc:
        logger.error("Failed to write reddit scraper_health: %s", exc)


def _persist_post(
    conn: sqlite3.Connection,
    composite_id: str,
    subreddit: str,
    post: dict[str, Any],
    priority: str = "medium",
) -> int | None:
    """Insert a Reddit post as a signal. Returns signal_id or None (dedup)."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT signal_id FROM reddit_post_ids WHERE post_id = ?", (composite_id,)
        ).fetchone()
        if existing:
            conn.execute("ROLLBACK")
            return None

        title = post.get("title", "")
        selftext = (post.get("selftext") or "").strip()
        # Combine title + body, truncated
        combined = title
        if selftext and selftext not in ("[removed]", "[deleted]"):
            combined += "\n\n" + selftext
        raw_text = combined[:_MAX_TEXT_CHARS] or None

        ts_utc = _ts_from_epoch(post.get("created_utc"))
        flair = post.get("link_flair_text") or post.get("author_flair_text")
        category = _classify_flair(flair)

        cur = conn.execute(
            """
            INSERT INTO signals
                (source, source_id, ts_utc, signal_type, raw_text,
                 source_server, has_attachments, signal_category, priority)
            VALUES ('reddit', ?, ?, 'post', ?, ?, 0, ?, ?)
            """,
            (composite_id, ts_utc, raw_text, subreddit, category, priority),
        )
        signal_id = cur.lastrowid

        conn.execute(
            "INSERT INTO reddit_post_ids (post_id, signal_id) VALUES (?, ?)",
            (composite_id, signal_id),
        )
        conn.execute("COMMIT")
        return signal_id
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise


class RedditAuthError(Exception):
    """Raised when Reddit blocks unauthenticated access (403)."""


async def _fetch_subreddit_posts(
    subreddit: str, listing: str, limit: int
) -> list[dict[str, Any]]:
    """Fetch /r/sub/listing.json. Returns list of post dicts.
    Raises RedditAuthError on 403 (credentials required).
    Returns empty list on 404 (sub not found).
    """
    url = f"{_BASE}/r/{subreddit}/{listing}.json?limit={limit}&raw_json=1"
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 403:
                raise RedditAuthError(
                    f"Reddit returned 403 for r/{subreddit}. "
                    "The old.reddit.com JSON endpoint is being blocked. "
                    "Check User-Agent headers or try adding cookie-based auth."
                )
            if resp.status_code == 404:
                logger.warning("r/%s returned 404 — subreddit may not exist; skipping", subreddit)
                return []
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            return [c["data"] for c in children if c.get("kind") == "t3"]
    except RedditAuthError:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning("Reddit HTTP error for r/%s: %s", subreddit, exc)
        return []


async def job_reddit_new(db_path: str) -> None:
    """Fetch /new posts from all subreddits. Runs every 5 min via scheduler."""
    logger.info("JOB START  reddit_new")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    total = 0
    errors: list[str] = []

    try:
        for sub in _SUBREDDITS:
            try:
                posts = await _fetch_subreddit_posts(sub, "new", _NEW_LIMIT)
                for post in posts:
                    pid = _post_id(sub, post.get("id", hashlib.sha1(str(post).encode()).hexdigest()[:8]))
                    if _persist_post(conn, pid, sub, post, priority="medium") is not None:
                        total += 1
            except RedditAuthError as exc:
                logger.error("Reddit auth required: %s", exc)
                errors.append(str(exc))
                break  # All subs will fail with same error — stop early
            except Exception as exc:
                logger.error("reddit_new error for r/%s: %s", sub, exc)
                errors.append(f"r/{sub}: {exc}")
    finally:
        conn.close()

    if errors:
        _write_health(db_path, success=False, records=total, error="; ".join(errors))
    else:
        _write_health(db_path, success=True, records=total)

    logger.info("JOB DONE   reddit_new — %d new signals", total)


async def job_reddit_hot(db_path: str) -> None:
    """Fetch /hot posts from all subreddits. Runs every 30 min via scheduler."""
    logger.info("JOB START  reddit_hot")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    total = 0

    error: str | None = None
    try:
        for sub in _SUBREDDITS:
            try:
                posts = await _fetch_subreddit_posts(sub, "hot", _HOT_LIMIT)
                for post in posts:
                    pid = _post_id(sub, post.get("id", hashlib.sha1(str(post).encode()).hexdigest()[:8]))
                    if _persist_post(conn, pid, sub, post, priority="high") is not None:
                        total += 1
            except RedditAuthError as exc:
                logger.error("Reddit auth required: %s", exc)
                error = str(exc)
                break
            except Exception as exc:
                logger.error("reddit_hot error for r/%s: %s", sub, exc)
    finally:
        conn.close()

    if error:
        _write_health(db_path, success=False, records=total, error=error)
    else:
        _write_health(db_path, success=True, records=total)
    logger.info("JOB DONE   reddit_hot — %d new signals", total)
