"""
APScheduler background runner for FCPriceMaster.
Entry point: uv run python -m src.workers.scheduler

Jobs:
  futgg_trending_pc      — every 20 min, first run at +30s
  futgg_trending_console — every 20 min, first run at +90s (offset so they don't collide)
  scraper_health_prune   — daily 03:00 UTC, removes rows older than 30 days
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.db.migrate import run_migrations
from src.scrapers.futgg import FutGGScraper

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DB_PATH = str(Path(__file__).parents[3] / "data" / "fcpricemaster.db")
_LOG_DIR = Path(__file__).parents[3] / "data" / "logs"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = RotatingFileHandler(
        _LOG_DIR / "scheduler.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------

async def job_trending(scraper: FutGGScraper, platform: str) -> None:
    """Fetch trending cards for one platform. Exceptions are caught and logged."""
    start = datetime.now(timezone.utc)
    logger.info("JOB START  futgg_trending_%s", platform)
    try:
        cards = await scraper.fetch_hot_cards(platform=platform, limit=500)  # type: ignore[arg-type]
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info(
            "JOB DONE   futgg_trending_%s — %d cards in %.1fs",
            platform, len(cards), elapsed,
        )
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.error(
            "JOB FAILED futgg_trending_%s after %.1fs — %s: %s",
            platform, elapsed, type(exc).__name__, exc,
        )
        # DO NOT re-raise — job exceptions must never kill the scheduler


async def job_prune_health(db_path: str) -> None:
    """Delete scraper_health rows older than 30 days."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        con = sqlite3.connect(db_path)
        n = con.execute(
            "DELETE FROM scraper_health WHERE run_at_utc < ?", (cutoff,)
        ).rowcount
        con.commit()
        con.close()
        logger.info("JOB DONE   health_prune — deleted %d old rows", n)
    except Exception as exc:
        logger.error("JOB FAILED health_prune — %s: %s", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Scheduler builder (extracted for testability)
# ---------------------------------------------------------------------------

def build_scheduler(
    scraper: FutGGScraper,
    db_path: str,
    *,
    pc_first_run: datetime | None = None,
    console_first_run: datetime | None = None,
) -> AsyncIOScheduler:
    """
    Build and configure an AsyncIOScheduler with all jobs registered.
    Does NOT call scheduler.start() — caller does that.
    """
    now = datetime.now(timezone.utc)
    pc_first = pc_first_run or (now + timedelta(seconds=30))
    con_first = console_first_run or (now + timedelta(seconds=90))

    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        job_trending,
        trigger=IntervalTrigger(minutes=20, timezone="UTC"),
        args=[scraper, "pc"],
        id="futgg_trending_pc",
        name="FUT.GG trending — PC",
        next_run_time=pc_first,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_trending,
        trigger=IntervalTrigger(minutes=20, start_date=now + timedelta(minutes=10), timezone="UTC"),
        args=[scraper, "console"],
        id="futgg_trending_console",
        name="FUT.GG trending — Console",
        next_run_time=con_first,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_prune_health,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        args=[db_path],
        id="scraper_health_prune",
        name="Scraper health prune",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    return scheduler


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(db_path: str = _DB_PATH) -> None:
    """Start the scheduler and block until SIGINT/SIGTERM."""
    setup_logging()
    logger.info("FCPriceMaster scheduler starting (pid=%d)", __import__("os").getpid())

    run_migrations(db_path)

    scraper = FutGGScraper(db_path=db_path)
    await scraper.__aenter__()

    scheduler = build_scheduler(scraper, db_path)
    scheduler.start()

    for job in scheduler.get_jobs():
        logger.info("  registered: %-40s next=%s", job.name, job.next_run_time)

    stop_event = asyncio.Event()

    def _on_signal(*_: Any) -> None:
        logger.info("Shutdown signal received — draining jobs...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, OSError):
            # Windows: loop.add_signal_handler not supported for SIGTERM
            signal.signal(sig, lambda _s, _f: _on_signal())

    logger.info("Scheduler running. Ctrl+C to stop.")
    await stop_event.wait()

    logger.info("Shutting down scheduler (wait up to 30s for in-flight jobs)...")
    scheduler.shutdown(wait=True)
    logger.info("Scheduler stopped.")

    logger.info("Closing Playwright browser...")
    await scraper.__aexit__(None, None, None)
    logger.info("Playwright browser closed. Exiting cleanly.")


if __name__ == "__main__":
    asyncio.run(run())
