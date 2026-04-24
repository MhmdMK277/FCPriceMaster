"""Abstract scraper bases: HttpxScraperBase and PlaywrightScraperBase.

Both enforce the schema-guard pattern: every scraper defines an expected schema,
validates parsed data against it, and writes a row to scraper_health on every run
(success or failure). Silent partial returns are never allowed.
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth as _Stealth

_stealth = _Stealth()

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]


class SchemaGuardError(Exception):
    """Raised when scraped data fails schema validation."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_health(
    db_path: str,
    source: str,
    success: bool,
    records_written: int = 0,
    error_text: str | None = None,
    schema_diff: str | None = None,
) -> None:
    """Write one row to scraper_health. Never raises — health writes must not mask real errors."""
    try:
        con = sqlite3.connect(db_path)
        # Get previous consecutive_failures count
        row = con.execute(
            "SELECT consecutive_failures FROM scraper_health WHERE source=? ORDER BY run_at_utc DESC LIMIT 1",
            (source,),
        ).fetchone()
        prev_failures = row[0] if row else 0
        consecutive = 0 if success else prev_failures + 1
        con.execute(
            """INSERT INTO scraper_health
               (source, run_at_utc, success, records_written, consecutive_failures, last_error, schema_diff)
               VALUES (?,?,?,?,?,?,?)""",
            (source, _now_utc(), 1 if success else 0, records_written, consecutive, error_text, schema_diff),
        )
        con.commit()
        con.close()
    except Exception as exc:
        logger.error("Failed to write scraper_health row: %s", exc)


class ScraperBase(ABC):
    """Common contract shared by both httpx and Playwright scrapers."""

    source_name: str = "unknown"

    @abstractmethod
    def expected_schema(self) -> dict[str, type]:
        """Return {field_name: expected_type} for every required field in parsed data."""

    def validate(self, parsed: dict[str, Any]) -> None:
        """Raise SchemaGuardError with a diff if parsed data doesn't match expected_schema."""
        schema = self.expected_schema()
        missing = [k for k in schema if k not in parsed]
        wrong_types = [
            f"{k}: expected {schema[k].__name__}, got {type(parsed[k]).__name__}"
            for k in schema
            if k in parsed and not isinstance(parsed[k], schema[k])
        ]
        if missing or wrong_types:
            diff = ""
            if missing:
                diff += f"Missing fields: {missing}. "
            if wrong_types:
                diff += f"Wrong types: {wrong_types}."
            raise SchemaGuardError(diff.strip())


# ---------------------------------------------------------------------------
# Httpx-based scraper (for future static/JSON sources)
# ---------------------------------------------------------------------------

class HttpxScraperBase(ScraperBase, ABC):
    def __init__(
        self,
        base_url: str,
        db_path: str,
        rate_limit_seconds: float = 2.5,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.db_path = db_path
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout
        self._last_request_at: float = 0.0

    async def _jitter_wait(self) -> None:
        import time
        elapsed = time.monotonic() - self._last_request_at
        wait = self.rate_limit_seconds + random.uniform(0, 1.5)
        if elapsed < wait:
            await asyncio.sleep(wait - elapsed)
        self._last_request_at = time.monotonic()

    async def fetch(self, path: str, **kwargs: Any) -> httpx.Response:
        await self._jitter_wait()
        ua = random.choice(_USER_AGENTS)
        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", ua)
        headers.setdefault("Accept", "application/json, text/html, */*")
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                    resp = await client.get(self.base_url + path, headers=headers, **kwargs)
                    resp.raise_for_status()
                    return resp
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    @abstractmethod
    async def parse(self, response: httpx.Response) -> list[dict[str, Any]]:
        """Parse the HTTP response into a list of data dicts."""

    async def run(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            resp = await self.fetch(path, **kwargs)
            items = await self.parse(resp)
            for item in items:
                self.validate(item)
            _write_health(self.db_path, self.source_name, success=True, records_written=len(items))
            return items
        except SchemaGuardError as exc:
            _write_health(self.db_path, self.source_name, success=False, error_text=str(exc), schema_diff=str(exc))
            raise
        except Exception as exc:
            _write_health(self.db_path, self.source_name, success=False, error_text=str(exc))
            raise


# ---------------------------------------------------------------------------
# Playwright-based scraper (for JS-rendered pages like FUT.GG)
# ---------------------------------------------------------------------------

class PlaywrightScraperBase(ScraperBase, ABC):
    """
    Manages a single long-lived Playwright browser + context across all calls.
    Use as an async context manager:

        async with FutGGScraper(db_path) as scraper:
            cards = await scraper.fetch_hot_cards('pc')
    """

    def __init__(
        self,
        db_path: str,
        rate_limit_min: float = 5.0,
        rate_limit_max: float = 10.0,
        headless: bool = True,
    ) -> None:
        self.db_path = db_path
        self.rate_limit_min = rate_limit_min
        self.rate_limit_max = rate_limit_max
        self.headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._last_nav_at: float = 0.0

    async def __aenter__(self) -> "PlaywrightScraperBase":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=random.choice(_USER_AGENTS),
            locale="en-US",
            timezone_id="Europe/London",
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _new_page(self) -> Page:
        assert self._context is not None, "Use as async context manager"
        page = await self._context.new_page()
        await _stealth.apply_stealth_async(page)
        return page

    async def _jitter_wait(self) -> None:
        import time
        elapsed = time.monotonic() - self._last_nav_at
        wait = random.uniform(self.rate_limit_min, self.rate_limit_max)
        if elapsed < wait:
            await asyncio.sleep(wait - elapsed)
        self._last_nav_at = time.monotonic()

    async def _navigate(self, page: Page, url: str, wait_until: str = "domcontentloaded") -> None:
        await self._jitter_wait()
        logger.debug("Navigating to %s", url)
        await page.goto(url, wait_until=wait_until, timeout=90000)
        await self._dismiss_cmp(page)

    @staticmethod
    async def _dismiss_cmp(page: Page) -> None:
        """Remove cookie consent overlay that blocks clicks."""
        await page.evaluate(
            "() => document.querySelectorAll('#cmpwrapper, .cmpwrapper').forEach(el => el.remove())"
        )

    async def _set_platform(self, page: Page, platform: str) -> None:
        """Click the platform selector dropdown and choose Console or PC."""
        label = "PC" if platform == "pc" else "Console"
        trigger = page.locator('[title="Select platform"]').first
        if not await trigger.is_visible(timeout=3000):
            logger.warning("Platform trigger not visible; skipping platform switch")
            return
        await trigger.click(force=True, timeout=5000)
        await page.wait_for_timeout(600)
        item = page.locator(f'[role="menuitem"]:has-text("{label}")').first
        await item.click(timeout=5000)
        await page.wait_for_timeout(1500)
        logger.debug("Platform switched to %s", platform)
