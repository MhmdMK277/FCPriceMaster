"""
Reddit ingestion job functions for FCPriceMaster.

Uses OAuth2 client-credentials flow when REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET are set.
If credentials are missing, logs a clear warning and writes one "disabled" health row — does
NOT write thousands of failure rows.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_OAUTH_BASE = "https://oauth.reddit.com"
_BASE = "https://old.reddit.com"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_DOTENV_PATH = os.path.join(_ROOT, ".env")
_SUBREDDITS = ["fut", "EASportsFC", "fut_economy"]
_NEW_LIMIT = 20
_HOT_LIMIT = 10
_MAX_TEXT_CHARS = 5000

# ---------------------------------------------------------------------------
# Credential state (module-level, checked once then cached)
# ---------------------------------------------------------------------------

_creds_checked = False
_creds_available = False
_disabled_health_written = False
_active_subreddits = list(_SUBREDDITS)
_skipped_subreddits: set[str] = set()

# OAuth2 token cache
_token_cache: dict[str, Any] = {}  # keys: token, expires_at

load_dotenv(_DOTENV_PATH)


def _user_agent() -> str:
    username = os.environ.get("REDDIT_USERNAME", "YOUR_USERNAME").strip() or "YOUR_USERNAME"
    return f"FCPriceMaster/1.0 by /u/{username}"


def _check_credentials() -> bool:
    """Return True if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are in env."""
    global _creds_checked, _creds_available
    if not _creds_checked:
        _creds_available = bool(
            os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET")
        )
        _creds_checked = True
        if not _creds_available:
            logger.warning(
                "Reddit OAuth credentials not set — Reddit ingestion disabled. "
                "Add REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to .env to enable."
            )
    return _creds_available


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _write_disabled_health(db_path: str) -> None:
    """Write exactly one 'disabled' health row — idempotent."""
    global _disabled_health_written
    if _disabled_health_written:
        return
    try:
        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO scraper_health (source, run_at_utc, success, records_written, "
            "consecutive_failures, last_error) VALUES ('reddit', ?, 0, 0, 0, ?)",
            (_now_utc(), "Reddit OAuth credentials not set"),
        )
        con.commit()
        con.close()
        _disabled_health_written = True
    except Exception as exc:
        logger.error("Failed to write reddit disabled health row: %s", exc)


# ---------------------------------------------------------------------------
# OAuth2 token management
# ---------------------------------------------------------------------------

async def _get_access_token() -> str | None:
    """Return a valid OAuth2 access token, refreshing if expired."""
    if _token_cache.get("token") and _token_cache.get("expires_at", 0) > time.time() + 60:
        return str(_token_cache["token"])

    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _TOKEN_URL,
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": _user_agent()},
            )
            resp.raise_for_status()
            data = resp.json()
            _token_cache["token"] = data["access_token"]
            _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
            logger.debug("Reddit OAuth2 token refreshed (expires in %ds)", data.get("expires_in", 3600))
            return str(_token_cache["token"])
    except Exception as exc:
        logger.error("Failed to get Reddit OAuth2 token: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Post persistence
# ---------------------------------------------------------------------------

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
    """Raised when Reddit blocks access (no token or 403)."""


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

async def _fetch_subreddit_posts(
    subreddit: str, listing: str, limit: int, token: str | None
) -> list[dict[str, Any]]:
    """Fetch /r/sub/listing.json using OAuth2 token if available.
    Raises RedditAuthError on 403 (credentials required or expired).
    Returns empty list on 404 (sub not found).
    """
    if token:
        url = f"{_OAUTH_BASE}/r/{subreddit}/{listing}/.json?limit={limit}&raw_json=1"
        headers = {
            "User-Agent": _user_agent(),
            "Authorization": f"Bearer {token}",
        }
    else:
        url = f"{_BASE}/r/{subreddit}/{listing}.json?limit={limit}&raw_json=1"
        headers = {
            "User-Agent": _user_agent(),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
        }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 403:
                raise RedditAuthError(
                    f"Reddit returned 403 for r/{subreddit}. "
                    "OAuth2 token may be invalid or expired. "
                    "Check REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env."
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


async def _subreddit_exists(subreddit: str) -> bool:
    """Return False only when old.reddit explicitly reports a missing/private subreddit."""
    url = f"{_BASE}/r/{subreddit}/about.json"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": _user_agent(), "Accept": "application/json"},
            )
            if resp.status_code == 404:
                return False
            if resp.status_code >= 400:
                return True
            data = resp.json()
            return data.get("error") != 404
    except Exception as exc:
        logger.debug("Subreddit existence check failed for r/%s: %s", subreddit, exc)
        return True


async def _active_poll_subreddits(db_path: str) -> list[str]:
    """Drop nonexistent/private subreddits from this worker session after one warning."""
    global _active_subreddits
    keep: list[str] = []
    for sub in _active_subreddits:
        if sub in _skipped_subreddits:
            continue
        if await _subreddit_exists(sub):
            keep.append(sub)
            continue
        logger.warning(
            "Subreddit r/%s does not exist or is private — skipping permanently this session",
            sub,
        )
        _write_health(db_path, success=False, records=0, error=f"Subreddit not found: r/{sub}")
        _skipped_subreddits.add(sub)
    _active_subreddits = keep
    return list(_active_subreddits)


# ---------------------------------------------------------------------------
# Job entry points
# ---------------------------------------------------------------------------

async def job_reddit_new(db_path: str) -> None:
    """Fetch /new posts from all subreddits. Runs every 5 min via scheduler."""
    if not _check_credentials():
        _write_disabled_health(db_path)
        return

    logger.info("JOB START  reddit_new")
    token = await _get_access_token()

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    total = 0
    errors: list[str] = []

    try:
        for sub in await _active_poll_subreddits(db_path):
            try:
                posts = await _fetch_subreddit_posts(sub, "new", _NEW_LIMIT, token)
                for post in posts:
                    pid = _post_id(sub, post.get("id", hashlib.sha1(str(post).encode()).hexdigest()[:8]))
                    if _persist_post(conn, pid, sub, post, priority="medium") is not None:
                        total += 1
            except RedditAuthError as exc:
                logger.error("Reddit auth error: %s", exc)
                errors.append(str(exc))
                break
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
    if not _check_credentials():
        _write_disabled_health(db_path)
        return

    logger.info("JOB START  reddit_hot")
    token = await _get_access_token()

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    total = 0
    error: str | None = None

    try:
        for sub in await _active_poll_subreddits(db_path):
            try:
                posts = await _fetch_subreddit_posts(sub, "hot", _HOT_LIMIT, token)
                for post in posts:
                    pid = _post_id(sub, post.get("id", hashlib.sha1(str(post).encode()).hexdigest()[:8]))
                    if _persist_post(conn, pid, sub, post, priority="high") is not None:
                        total += 1
            except RedditAuthError as exc:
                logger.error("Reddit auth error: %s", exc)
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
