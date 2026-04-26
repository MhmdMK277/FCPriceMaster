"""
Twitter/X ingestion worker for FCPriceMaster.
Entry point: uv run python -m src.workers.twitter_ingest

Strategy: visit each leaker account's profile page directly instead of the home
timeline.  The home/following tab approach was unreliable — X kept serving "For You"
content regardless of the tab clicked.  Profile-by-profile guarantees we only ever
read tweets from the exact handles we care about.

Cadence: one profile page load every ~20 s, cycling through all configured accounts.
With 3 accounts that is ~60 s per full cycle.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import signal
import sqlite3
import sys
import time as _time
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

_PROFILE_BASE_URL = "https://x.com/{handle}"
_INTER_PROFILE_DELAY = 20   # seconds between profile page loads
_INTER_PROFILE_JITTER = 5   # ± random jitter added to above
_MIN_CYCLE_INTERVAL = 60    # minimum seconds between full cycles
_RATE_LIMIT_BACKOFF = 300   # 5 min backoff on rate limit
_MAX_CONSECUTIVE_EMPTY = 10 # empty profiles before ERROR log

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
        logger.warning("twitter_accounts.yaml not found at %s — worker will do nothing", _ACCOUNTS_PATH)
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
                # preserve original casing for URL construction
                "handle_raw": acc.get("handle", ""),
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
    if "account/suspended" in url:
        return "login_required"
    rate_limit_el = await page.query_selector('[data-testid="empty_state_body"]')
    if rate_limit_el:
        text = (await rate_limit_el.inner_text()).lower()
        if "rate limit" in text or "too many requests" in text:
            return "rate_limited"
    return "ok"


async def _extract_tweets(page: Page) -> list[dict[str, Any]]:
    """
    Extract tweet data from visible article[data-testid="tweet"] elements.
    Returns list of raw dicts.
    """
    articles = await page.query_selector_all('article[data-testid="tweet"]')
    if not articles:
        return []

    results: list[dict[str, Any]] = []

    for article in articles:
        try:
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

            text_el = await article.query_selector('[data-testid="tweetText"]')
            text = await text_el.inner_text() if text_el else ""

            time_el = await article.query_selector("time[datetime]")
            timestamp = ""
            if time_el:
                dt_attr = await time_el.get_attribute("datetime")
                if dt_attr:
                    try:
                        dt = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
                        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        timestamp = _now_utc()
            if not timestamp:
                timestamp = _now_utc()

            tweet_id = None
            links = await article.query_selector_all('a[href*="/status/"]')
            for link in links:
                href = await link.get_attribute("href")
                tweet_id = parse_tweet_id_from_href(href or "")
                if tweet_id:
                    break

            media_els = await article.query_selector_all('img[src*="pbs.twimg.com/media"]')
            media_urls = []
            for img in media_els:
                src = await img.get_attribute("src")
                if src:
                    media_urls.append(src)

            if not handle and not text:
                continue

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
# Worker
# ---------------------------------------------------------------------------

class TwitterIngestWorker:
    def __init__(self, db_path: str = _DB_PATH, cookie_path: Path = _COOKIE_PATH) -> None:
        self.db_path = db_path
        self.cookie_path = cookie_path
        self._conn: sqlite3.Connection | None = None
        self._context: BrowserContext | None = None
        self._playwright_obj: Any = None
        self._account_config: dict[str, dict[str, str]] = {}
        self._consecutive_empty = 0

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = open_db(self.db_path)
        return self._conn

    async def start(self) -> None:
        self._account_config = load_account_config()
        handles = sorted(self._account_config.keys())
        logger.info(
            "Twitter allowlist: %d handles: %s",
            len(handles),
            ", ".join(handles) or "(none — worker will do nothing)",
        )
        if not handles:
            logger.warning(
                "Twitter allowlist is EMPTY — no profiles to scrape. "
                "Check config/twitter_accounts.yaml."
            )

        self._playwright_obj = await async_playwright().start()
        self._context = await _build_context(self._playwright_obj, self.cookie_path)

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright_obj:
            await self._playwright_obj.stop()
        if self._conn:
            self._conn.close()

    async def poll_profile(self, handle_lower: str, page: Page) -> int:
        """
        Load https://x.com/{handle}, extract tweets, keep ONLY tweets whose
        extracted handle matches the profile we loaded.  Returns new signal count.
        """
        meta = self._account_config.get(handle_lower, {})
        # Use original casing for URL so X resolves it correctly
        handle_raw = meta.get("handle_raw", handle_lower)
        url = _PROFILE_BASE_URL.format(handle=handle_raw)

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        state = await _check_page_state(page)
        if state == "login_required":
            raise RuntimeError("login_required")
        if state == "rate_limited":
            raise RuntimeError("rate_limited")

        raw_tweets = await _extract_tweets(page)
        logger.info("@%s profile: %d tweet articles found", handle_raw, len(raw_tweets))

        if not raw_tweets:
            self._consecutive_empty += 1
            return 0

        # Profile guard: only keep tweets whose handle matches the profile we loaded.
        # Retweets and quoted tweets can surface other handles in the DOM; we only
        # want the profile owner's own tweets.
        filtered: list[dict[str, Any]] = []
        for t in raw_tweets:
            extracted = t.get("handle", "").lower()
            if extracted == handle_lower:
                filtered.append(t)
            else:
                logger.debug(
                    "Dropping @%s tweet on @%s profile page — handle mismatch",
                    extracted, handle_raw,
                )

        # Allowlist backstop: guard against config drift
        if handle_lower not in self._account_config:
            logger.warning(
                "Dropping tweet from @%s — not in allowlist (this should not happen)",
                handle_lower,
            )
            return 0

        if not filtered:
            self._consecutive_empty += 1
            return 0

        self._consecutive_empty = 0
        conn = self._get_conn()
        new_count = 0
        for raw in filtered:
            data = parse_tweet_data(raw, self._account_config)
            signal_id = persist_tweet(conn, data)
            if signal_id is not None:
                new_count += 1
                logger.info(
                    "TWEET ingested id=%s @%s %.80r",
                    signal_id, data["handle"], data["raw_text"],
                )

        return new_count

    async def run(self, stop_event: asyncio.Event) -> None:
        """Profile-cycling loop. Stops when stop_event is set."""
        rate_limit_until: float = 0.0

        while not stop_event.is_set():
            handles = list(self._account_config.keys())
            if not handles:
                logger.warning("No handles configured — sleeping 60s")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
                continue

            cycle_start = _time.monotonic()
            cycle_new = 0

            for handle_lower in handles:
                if stop_event.is_set():
                    break

                now = _time.monotonic()
                if now < rate_limit_until:
                    wait = rate_limit_until - now
                    logger.info("Rate-limited — waiting %.0fs", wait)
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=min(wait, 60))
                    except asyncio.TimeoutError:
                        pass
                    if now + min(wait, 60) < rate_limit_until:
                        continue

                page: Page = await self._context.new_page()
                await _stealth.apply_stealth_async(page)
                try:
                    new = await self.poll_profile(handle_lower, page)
                    cycle_new += new
                    _write_health(self.db_path, success=True, records_written=new)

                    if self._consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                        logger.error(
                            "Twitter: %d consecutive empty profile polls — "
                            "possible DOM change or all accounts inactive.",
                            self._consecutive_empty,
                        )

                except RuntimeError as exc:
                    err = str(exc)
                    if err == "login_required":
                        logger.error(
                            "Twitter cookies expired. Re-export from browser, "
                            "save to data/.cookies/x_cookies.txt, restart worker."
                        )
                        _write_health(self.db_path, success=False, error_text="cookies_expired")
                        return
                    elif err == "rate_limited":
                        logger.warning("Twitter rate limit — backing off %ds", _RATE_LIMIT_BACKOFF)
                        _write_health(self.db_path, success=False, error_text="rate_limited")
                        rate_limit_until = _time.monotonic() + _RATE_LIMIT_BACKOFF
                    else:
                        logger.error("Twitter poll error (@%s): %s", handle_lower, exc)
                        _write_health(self.db_path, success=False, error_text=err)

                except Exception as exc:
                    logger.error("Twitter unexpected error (@%s): %s: %s", handle_lower, type(exc).__name__, exc)
                    _write_health(self.db_path, success=False, error_text=str(exc))

                finally:
                    await page.close()

                # Jittered delay between profile loads
                if not stop_event.is_set() and handle_lower != handles[-1]:
                    delay = _INTER_PROFILE_DELAY + random.uniform(
                        -_INTER_PROFILE_JITTER, _INTER_PROFILE_JITTER
                    )
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=max(delay, 1))
                    except asyncio.TimeoutError:
                        pass

            logger.info("Cycle complete: %d new tweets ingested across %d profiles", cycle_new, len(handles))

            # Wait out the remainder of the minimum cycle interval
            elapsed = _time.monotonic() - cycle_start
            remaining = _MIN_CYCLE_INTERVAL - elapsed
            if remaining > 0 and not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=remaining)
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

    logger.info(
        "Twitter ingest worker running. Profile interval: ~%ds, cycle: ~%ds",
        _INTER_PROFILE_DELAY, _MIN_CYCLE_INTERVAL,
    )
    await worker.run(stop_event)
    await worker.stop()
    logger.info("Twitter ingest worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run())
