"""
Twitter/X ingestion worker for FCPriceMaster.
Entry point: uv run python -m src.workers.twitter_ingest

Strategy: poll the Following timeline (/home) every ~50 seconds.
One page load per cycle; all followed accounts' new tweets in a single DOM read.
Achieves <60s detection latency without navigating to individual profiles.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import yaml
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth as _Stealth

from src.utils.cookie_loader import load_netscape_cookies

_ROOT = Path(__file__).parents[3]
_DB_PATH = str(_ROOT / "data" / "fcpricemaster.db")
_LOG_DIR = _ROOT / "data" / "logs"
_COOKIE_PATH = _ROOT / "data" / ".cookies" / "x_cookies.txt"
_ACCOUNTS_PATH = _ROOT / "config" / "twitter_accounts.yaml"

_POLL_INTERVAL = 50          # seconds between timeline polls
_RATE_LIMIT_BACKOFF = 300    # 5 min initial backoff on rate limit
_MAX_CONSECUTIVE_EMPTY = 5   # empty polls before ERROR
_HOME_URL = "https://x.com/home"

_stealth = _Stealth()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = RotatingFileHandler(
        _LOG_DIR / "twitter_ingest.log",
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


def load_account_config() -> dict[str, dict[str, str]]:
    """Return {handle_lower: {category, priority}} from twitter_accounts.yaml."""
    if not _ACCOUNTS_PATH.exists():
        logger.warning("twitter_accounts.yaml not found at %s — no category metadata", _ACCOUNTS_PATH)
        return {}
    with open(_ACCOUNTS_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    result: dict[str, dict[str, str]] = {}
    for acc in cfg.get("accounts", []):
        handle = acc.get("handle", "").lower()
        if handle:
            result[handle] = {
                "category": acc.get("category", "discussion"),
                "priority": acc.get("priority", "medium"),
            }
    return result


def open_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_health(
    db_path: str,
    success: bool,
    records_written: int = 0,
    error_text: str | None = None,
) -> None:
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT consecutive_failures FROM scraper_health WHERE source='twitter' ORDER BY run_at_utc DESC LIMIT 1"
        ).fetchone()
        prev = row[0] if row else 0
        consecutive = 0 if success else prev + 1
        con.execute(
            "INSERT INTO scraper_health (source, run_at_utc, success, records_written, consecutive_failures, last_error) "
            "VALUES ('twitter', ?, ?, ?, ?, ?)",
            (_now_utc(), 1 if success else 0, records_written, consecutive, error_text),
        )
        con.commit()
        con.close()
    except Exception as exc:
        logger.error("Failed to write scraper_health: %s", exc)


# ---------------------------------------------------------------------------
# Tweet parsing (pure functions — testable without Playwright)
# ---------------------------------------------------------------------------

def parse_tweet_id_from_href(href: str) -> str | None:
    """Extract tweet ID from a /username/status/123456 href."""
    if not href:
        return None
    parts = href.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2] == "status":
        candidate = parts[-1]
        if candidate.isdigit():
            return candidate
    return None


def generate_tweet_id(handle: str, ts: str) -> str:
    """Fallback tweet ID when permalink is not available."""
    raw = f"{handle}:{ts}"
    return "fallback_" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def parse_tweet_data(raw: dict[str, Any], account_config: dict[str, dict[str, str]]) -> dict[str, Any]:
    """
    Convert raw DOM-extracted tweet dict into a signal-ready dict.
    raw keys: handle, text, timestamp, media_urls, tweet_id (optional).
    """
    handle = raw.get("handle", "unknown")
    ts = raw.get("timestamp", _now_utc())
    tweet_id = raw.get("tweet_id") or generate_tweet_id(handle, ts)

    meta = account_config.get(handle.lower(), {})
    return {
        "tweet_id": tweet_id,
        "handle": handle,
        "ts_utc": ts,
        "raw_text": raw.get("text") or None,
        "signal_category": meta.get("category", "discussion"),
        "priority": meta.get("priority", "medium"),
        "has_attachments": 1 if raw.get("media_urls") else 0,
        "media_urls": raw.get("media_urls", []),
    }


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def persist_tweet(conn: sqlite3.Connection, data: dict[str, Any]) -> int | None:
    """
    Atomically insert tweet signal. Returns signal_id or None (dedup).
    Uses BEGIN IMMEDIATE to prevent concurrent insert races.
    """
    tweet_id = data["tweet_id"]
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT signal_id FROM twitter_tweet_ids WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        if existing:
            conn.execute("ROLLBACK")
            return None

        cur = conn.execute(
            """
            INSERT INTO signals
                (source, source_id, ts_utc, signal_type, raw_text,
                 source_server, has_attachments, signal_category, priority)
            VALUES ('twitter', ?, ?, 'tweet', ?, ?, ?, ?, ?)
            """,
            (
                tweet_id,
                data["ts_utc"],
                data["raw_text"],
                data["handle"],
                data["has_attachments"],
                data["signal_category"],
                data["priority"],
            ),
        )
        signal_id = cur.lastrowid

        for url in data.get("media_urls", []):
            conn.execute(
                "INSERT INTO signal_attachments (signal_id, url, content_type) VALUES (?, ?, ?)",
                (signal_id, url, "image/jpeg"),
            )

        conn.execute(
            "INSERT INTO twitter_tweet_ids (tweet_id, signal_id) VALUES (?, ?)",
            (tweet_id, signal_id),
        )
        conn.execute("COMMIT")
        return signal_id
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def _build_context(playwright_obj: Any, cookie_path: Path) -> BrowserContext:
    """Launch headless Chromium, load Twitter cookies, return context."""
    browser: Browser = await playwright_obj.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context: BrowserContext = await browser.new_context(
        viewport={"width": 1400, "height": 900},
        user_agent=_USER_AGENT,
        locale="en-US",
        timezone_id="Europe/London",
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    cookies = load_netscape_cookies(cookie_path)
    await context.add_cookies(cookies)
    logger.info("Loaded %d cookies from %s", len(cookies), cookie_path)
    return context


async def _check_page_state(page: Page) -> str:
    """Returns 'ok', 'login_required', or 'rate_limited'."""
    url = page.url
    if "login" in url or "i/flow" in url:
        return "login_required"
    # Twitter's actual rate-limit page replaces all content with a simple message;
    # check for it specifically by looking for the rate-limit error element.
    # Avoid checking body text — it's too broad and matches false positives.
    rate_limit_el = await page.query_selector('[data-testid="empty_state_body"]')
    if rate_limit_el:
        text = (await rate_limit_el.inner_text()).lower()
        if "rate limit" in text or "too many requests" in text:
            return "rate_limited"
    # Also check URL-based rate limit indicator
    if "account/suspended" in url:
        return "login_required"
    return "ok"


async def _extract_tweets(page: Page) -> list[dict[str, Any]]:
    """
    Extract tweet data from visible article[data-testid="tweet"] elements.
    Returns list of raw dicts. Logs schema-guard warnings if structure changes.
    """
    articles = await page.query_selector_all('article[data-testid="tweet"]')
    if not articles:
        return []

    results: list[dict[str, Any]] = []

    for article in articles:
        try:
            # Author handle
            handle = ""
            user_name_el = await article.query_selector('[data-testid="User-Name"]')
            if user_name_el:
                user_text = await user_name_el.inner_text()
                # Format: "Display Name\n@handle"
                for part in user_text.split("\n"):
                    part = part.strip()
                    if part.startswith("@"):
                        handle = part[1:]
                        break

            # Tweet text
            text_el = await article.query_selector('[data-testid="tweetText"]')
            text = await text_el.inner_text() if text_el else ""

            # Timestamp
            time_el = await article.query_selector("time[datetime]")
            timestamp = ""
            if time_el:
                dt_attr = await time_el.get_attribute("datetime")
                if dt_attr:
                    # Convert ISO 8601 to our UTC format
                    try:
                        dt = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
                        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        timestamp = _now_utc()
            if not timestamp:
                timestamp = _now_utc()

            # Tweet ID from status link
            tweet_id = None
            links = await article.query_selector_all('a[href*="/status/"]')
            for link in links:
                href = await link.get_attribute("href")
                tweet_id = parse_tweet_id_from_href(href or "")
                if tweet_id:
                    break

            # Media
            media_els = await article.query_selector_all('img[src*="pbs.twimg.com/media"]')
            media_urls = []
            for img in media_els:
                src = await img.get_attribute("src")
                if src:
                    media_urls.append(src)

            if not handle and not text:
                continue  # completely empty — skip silently

            results.append({
                "handle": handle,
                "text": text.strip(),
                "timestamp": timestamp,
                "tweet_id": tweet_id,
                "media_urls": media_urls,
            })
        except Exception as exc:
            logger.debug("Failed to parse tweet article: %s", exc)

    return results


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

class TwitterIngestWorker:
    def __init__(self, db_path: str = _DB_PATH, cookie_path: Path = _COOKIE_PATH) -> None:
        self.db_path = db_path
        self.cookie_path = cookie_path
        self._conn: sqlite3.Connection | None = None
        self._context: BrowserContext | None = None
        self._browser: Browser | None = None
        self._playwright_obj: Any = None
        self._account_config: dict[str, dict[str, str]] = {}
        self._consecutive_empty = 0
        self._seen_on_startup: set[str] = set()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = open_db(self.db_path)
        return self._conn

    async def start(self) -> None:
        self._account_config = load_account_config()
        logger.info("Account config loaded: %d handles", len(self._account_config))

        self._playwright_obj = await async_playwright().start()
        self._context = await _build_context(self._playwright_obj, self.cookie_path)
        self._browser = self._context.browser

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright_obj:
            await self._playwright_obj.stop()
        if self._conn:
            self._conn.close()

    async def poll_once(self) -> int:
        """
        Navigate to /home, extract visible tweets, persist new ones.
        Returns count of new signals ingested. Raises on fatal errors.
        """
        page: Page = await self._context.new_page()
        await _stealth.apply_stealth_async(page)
        try:
            await page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)  # let timeline JS render

            state = await _check_page_state(page)
            if state == "login_required":
                raise RuntimeError("login_required")
            if state == "rate_limited":
                raise RuntimeError("rate_limited")

            raw_tweets = await _extract_tweets(page)
            logger.info("Timeline: found %d tweet articles", len(raw_tweets))

            if not raw_tweets:
                self._consecutive_empty += 1
                return 0

            self._consecutive_empty = 0
            conn = self._get_conn()
            new_count = 0

            for raw in raw_tweets:
                data = parse_tweet_data(raw, self._account_config)
                signal_id = persist_tweet(conn, data)
                if signal_id is not None:
                    new_count += 1
                    logger.info(
                        "TWEET ingested id=%s @%s %.80r",
                        signal_id, data["handle"], data["raw_text"],
                    )

            return new_count

        finally:
            await page.close()

    async def run(self, stop_event: asyncio.Event) -> None:
        """Polling loop. Stops when stop_event is set."""
        rate_limit_until: float = 0.0
        import time

        # First poll — collect tweet IDs already on timeline (don't ingest as "new")
        # so we only ingest tweets that appear AFTER the worker starts.
        # We do this by running poll_once but discarding the results on the very first
        # run; subsequent runs will dedup via twitter_tweet_ids naturally.
        # Actually, we just ingest everything — the dedup table prevents double-inserts
        # on subsequent runs, and initial ingestion of recent tweets is fine (they'll
        # be in the DB as historical signals).

        while not stop_event.is_set():
            try:
                now = time.monotonic()
                if now < rate_limit_until:
                    wait = rate_limit_until - now
                    logger.info("Rate-limited — waiting %.0fs before next poll", wait)
                    await asyncio.sleep(min(wait, 60))
                    continue

                new = await self.poll_once()
                _write_health(self.db_path, success=True, records_written=new)

                if self._consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                    logger.error(
                        "Twitter: %d consecutive empty polls — possible DOM change. "
                        "Check selectors in twitter_ingest.py.",
                        self._consecutive_empty,
                    )
                    _write_health(
                        self.db_path, success=False,
                        error_text=f"{self._consecutive_empty} consecutive empty polls",
                    )
                    # Don't stop — keep trying; might be a temporary glitch.

            except RuntimeError as exc:
                err = str(exc)
                if err == "login_required":
                    logger.error(
                        "Twitter cookies expired or invalid. "
                        "Re-export from browser, save to data/.cookies/x_cookies.txt, "
                        "and restart the Twitter worker."
                    )
                    _write_health(self.db_path, success=False, error_text="cookies_expired")
                    # Stop polling — hammering a login page achieves nothing.
                    break
                elif err == "rate_limited":
                    logger.warning("Twitter rate limit — backing off %ds", _RATE_LIMIT_BACKOFF)
                    _write_health(self.db_path, success=False, error_text="rate_limited")
                    rate_limit_until = time.monotonic() + _RATE_LIMIT_BACKOFF
                else:
                    logger.error("Twitter poll error: %s", exc)
                    _write_health(self.db_path, success=False, error_text=err)

            except Exception as exc:
                logger.error("Twitter unexpected error: %s: %s", type(exc).__name__, exc)
                _write_health(self.db_path, success=False, error_text=str(exc))

            # Wait for next poll cycle (respect stop_event)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run(db_path: str = _DB_PATH) -> None:
    setup_logging()
    logger.info("FCPriceMaster Twitter ingest worker starting (pid=%d)", os.getpid())

    worker = TwitterIngestWorker(db_path=db_path)

    try:
        await worker.start()
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Twitter worker cannot start: %s", exc)
        return

    stop_event = asyncio.Event()

    def _on_signal(*_: Any) -> None:
        logger.info("Shutdown signal received — stopping Twitter worker...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, OSError):
            signal.signal(sig, lambda _s, _f: _on_signal())

    logger.info("Twitter ingest worker running. Poll interval: %ds", _POLL_INTERVAL)
    await worker.run(stop_event)
    await worker.stop()
    logger.info("Twitter ingest worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run())
