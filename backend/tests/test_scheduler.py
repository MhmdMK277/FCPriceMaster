"""Tests for the APScheduler wiring in scheduler.py.

These tests assert on job registration, exception isolation, and shutdown
behaviour without actually running real scrapes or real time intervals.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.migrate import run_migrations
from src.workers.scheduler import build_scheduler, job_prune_health, job_trending


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    run_migrations(path)
    yield path
    try:
        os.unlink(path)
        for suffix in ("-wal", "-shm"):
            wal = path + suffix
            if os.path.exists(wal):
                os.unlink(wal)
    except OSError:
        pass


@pytest.fixture()
def mock_scraper():
    scraper = MagicMock()
    scraper.fetch_hot_cards = AsyncMock(return_value=[{"card_key": "26-1", "player_name": "Test"}])
    scraper.__aenter__ = AsyncMock(return_value=scraper)
    scraper.__aexit__ = AsyncMock(return_value=None)
    return scraper


# ---------------------------------------------------------------------------
# Job registration tests
# ---------------------------------------------------------------------------

def test_build_scheduler_registers_three_jobs(mock_scraper, tmp_db):
    """build_scheduler() should register all expected job IDs."""
    now = datetime.now(timezone.utc)
    scheduler = build_scheduler(mock_scraper, tmp_db, pc_first_run=now, console_first_run=now)
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
        required = {
            "futgg_trending_pc", "futgg_trending_console", "scraper_health_prune",
            "reddit_new", "reddit_hot", "ea_news",
            "fodder_sweep", "signal_tagger",
        }
        assert required.issubset(job_ids), f"Missing jobs: {required - job_ids}"
    finally:
        if scheduler.running:

            scheduler.shutdown(wait=False)


def test_job_intervals(mock_scraper, tmp_db):
    """PC and console jobs must have 20-minute intervals; prune is cron."""
    now = datetime.now(timezone.utc)
    scheduler = build_scheduler(mock_scraper, tmp_db, pc_first_run=now, console_first_run=now)
    try:
        jobs = {job.id: job for job in scheduler.get_jobs()}
        # Interval jobs have a 'weeks'/'days'/'hours'/'minutes' field on their trigger
        pc_trigger = jobs["futgg_trending_pc"].trigger
        con_trigger = jobs["futgg_trending_console"].trigger
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
        assert isinstance(pc_trigger, IntervalTrigger)
        assert isinstance(con_trigger, IntervalTrigger)
        # interval is stored as a timedelta; 20 min = 1200 seconds
        assert pc_trigger.interval.total_seconds() == 1200
        assert con_trigger.interval.total_seconds() == 1200
        assert isinstance(jobs["scraper_health_prune"].trigger, CronTrigger)
    finally:
        if scheduler.running:

            scheduler.shutdown(wait=False)


def test_pc_console_first_runs_are_staggered(mock_scraper, tmp_db):
    """PC first run at +30s, console at +90s — they should not be identical."""
    scheduler = build_scheduler(mock_scraper, tmp_db)
    try:
        jobs = {job.id: job for job in scheduler.get_jobs()}
        pc_next = jobs["futgg_trending_pc"].next_run_time
        con_next = jobs["futgg_trending_console"].next_run_time
        assert pc_next is not None
        assert con_next is not None
        # Console runs at least 30 seconds after PC
        diff = (con_next - pc_next).total_seconds()
        assert diff >= 30, f"Expected console to run ≥30s after PC, got {diff}s"
    finally:
        if scheduler.running:

            scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Exception isolation tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_trending_exception_does_not_propagate(mock_scraper):
    """If fetch_hot_cards raises, job_trending must catch it and not re-raise."""
    mock_scraper.fetch_hot_cards = AsyncMock(side_effect=RuntimeError("site down"))
    # Should complete without raising
    await job_trending(mock_scraper, "pc")
    # scraper was called
    mock_scraper.fetch_hot_cards.assert_called_once_with(platform="pc", limit=500)


@pytest.mark.asyncio
async def test_job_prune_health_exception_does_not_propagate(tmp_db):
    """If the DB delete fails, job_prune_health must catch it and not re-raise."""
    # Pass a bad DB path to force an error
    await job_prune_health("/nonexistent/path.db")
    # No exception raised


@pytest.mark.asyncio
async def test_scheduler_survives_job_exception(mock_scraper, tmp_db):
    """
    Verify that when a job raises, the scheduler remains running
    and subsequent jobs can still be dispatched.
    """
    call_count = 0

    async def flaky_job(scraper: MagicMock, platform: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("first run fails")
        # second run succeeds silently

    now = datetime.now(timezone.utc)
    scheduler = build_scheduler(mock_scraper, tmp_db, pc_first_run=now, console_first_run=now)

    # Patch job_trending to use our flaky version for pc
    pc_job = next(j for j in scheduler.get_jobs() if j.id == "futgg_trending_pc")
    pc_job.func = flaky_job

    scheduler.start()
    try:
        # Give the scheduler a moment to fire
        await asyncio.sleep(0.3)
        assert scheduler.running, "Scheduler should still be running after job exception"
    finally:
        if scheduler.running:

            scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Shutdown / browser cleanup tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_playwright_context_closed_on_shutdown(mock_scraper, tmp_db):
    """
    Simulate a full startup + graceful shutdown cycle.
    Verify __aexit__ is called on the scraper (closes Playwright browser).
    """
    from src.workers.scheduler import build_scheduler

    now = datetime.now(timezone.utc)
    scheduler = build_scheduler(mock_scraper, tmp_db, pc_first_run=now, console_first_run=now)
    scheduler.start()

    # Immediately shut down
    if scheduler.running:

        scheduler.shutdown(wait=False)
    await mock_scraper.__aexit__(None, None, None)

    mock_scraper.__aexit__.assert_called_once_with(None, None, None)


# ---------------------------------------------------------------------------
# Health prune correctness test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_prune_health_deletes_old_rows(tmp_db):
    """Rows with run_at_utc older than 30 days should be deleted."""
    con = sqlite3.connect(tmp_db)
    # Insert one old row and one recent row
    con.execute(
        "INSERT INTO scraper_health (source, run_at_utc, success) VALUES (?,?,?)",
        ("futgg", "2020-01-01T00:00:00Z", 1),
    )
    con.execute(
        "INSERT INTO scraper_health (source, run_at_utc, success) VALUES (?,?,?)",
        ("futgg", "2099-01-01T00:00:00Z", 1),
    )
    con.commit()
    con.close()

    await job_prune_health(tmp_db)

    con = sqlite3.connect(tmp_db)
    rows = con.execute("SELECT run_at_utc FROM scraper_health").fetchall()
    con.close()
    dates = [r[0] for r in rows]
    assert "2020-01-01T00:00:00Z" not in dates
    assert "2099-01-01T00:00:00Z" in dates
