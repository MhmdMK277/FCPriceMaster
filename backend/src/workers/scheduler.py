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
from src.workers.reddit_ingest import job_reddit_new, job_reddit_hot
from src.workers.ea_ingest import job_ea_news
from src.workers.signal_tagger import job_signal_tagger
from src.llm.ask import _load_config
from src.llm.providers.registry import probe_all_providers
from src.llm.recommender import (
    generate_recommendations, evaluate_outcomes,
    _get_candidates, _has_worthy_candidates,
)

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
# Circuit breaker state (in-process, resets on restart)
# ---------------------------------------------------------------------------

_tier_pause_until: dict[str, datetime] = {}


def _get_consecutive_failures(db_path: str, source: str) -> int:
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT consecutive_failures FROM scraper_health WHERE source=? ORDER BY run_at_utc DESC LIMIT 1",
            (source,),
        ).fetchone()
        con.close()
        return row[0] if row else 0
    except Exception:
        return 0


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


async def job_fodder_sweep(scraper: FutGGScraper, db_path: str) -> None:  # noqa: ARG001
    """Sweep fodder prices for ratings 82-91 on both platforms."""
    start = datetime.now(timezone.utc)
    logger.info("JOB START  fodder_sweep")
    try:
        total = await scraper.fodder_sweep(
            ratings=list(range(82, 92)),
            platforms=["pc", "console"],
        )
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("JOB DONE   fodder_sweep — %d snapshots in %.1fs", total, elapsed)
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.error("JOB FAILED fodder_sweep after %.1fs — %s: %s", elapsed, type(exc).__name__, exc)


# Process-level lock: the scheduled job and the HTTP trigger both pass the
# per-card 10h recent-rec guard BEFORE either writes, so overlapping runs can
# insert contradictory recs for the same card (session 38: avoid id=1140 and
# buy id=1142 on the same card, seconds apart). One scheduler process owns
# port 8765, so a single asyncio lock covers every in-process caller.
_rec_lock = asyncio.Lock()


async def run_recommendations_safe(
    platform: str, db_path: str, provider_id: str
) -> list[dict] | None:
    """Run generate_recommendations under _rec_lock.

    Returns None IMMEDIATELY (no waiting) when a run is already in progress.
    The locked() pre-check is race-free here: all callers share one event
    loop and an uncontended Lock.acquire() does not yield control.
    """
    if _rec_lock.locked():
        return None
    async with _rec_lock:
        return await asyncio.to_thread(
            generate_recommendations, platform, db_path, 3, provider_id
        )


async def job_recommendations(platform: str, db_path: str) -> None:
    """Generate autonomous recommendations for one platform."""
    start = datetime.now(timezone.utc)
    # Scheduled runs use scheduled_provider from llm_config.yaml. The old
    # hardcoded default (haiku) meant every scheduled run silently produced
    # nothing while the Anthropic balance was $0 — free NVIDIA models work.
    provider_id = str(_load_config().get("scheduled_provider", "haiku"))
    logger.info("JOB START  recommendations_%s via %s", platform, provider_id)
    try:
        recs = await run_recommendations_safe(platform, db_path, provider_id)
        if recs is None:
            logger.info("JOB SKIP   recommendations_%s — run already in progress", platform)
            return
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("JOB DONE   recommendations_%s — %d recs in %.1fs", platform, len(recs), elapsed)
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.error("JOB FAILED recommendations_%s after %.1fs — %s: %s", platform, elapsed, type(exc).__name__, exc)


# Provider health cache. Refreshed by job_probe_providers every 30 min so the
# UI can poll POST /probe-providers without hammering NVIDIA. A model NVIDIA
# pulls (kimi-k2.6, 2026-07-17) shows healthy=False and auto-restores when the
# endpoint comes back.
_provider_health: dict[str, bool] = {}


async def job_probe_providers() -> None:
    """Probe all LLM providers and cache the results."""
    global _provider_health
    logger.info("JOB START  provider_probe")
    try:
        _provider_health = await probe_all_providers()
        unhealthy = sorted(pid for pid, ok in _provider_health.items() if not ok)
        logger.info(
            "JOB DONE   provider_probe — %d providers, unhealthy: %s",
            len(_provider_health), ", ".join(unhealthy) or "none",
        )
    except Exception as exc:
        logger.error("JOB FAILED provider_probe — %s: %s", type(exc).__name__, exc)


async def job_outcome_evaluator(db_path: str) -> None:
    """Evaluate recommendations older than 24h with no outcome."""
    logger.info("JOB START  outcome_evaluator")
    try:
        count = await asyncio.to_thread(evaluate_outcomes, db_path)
        logger.info("JOB DONE   outcome_evaluator — %d outcomes written", count)
    except Exception as exc:
        logger.error("JOB FAILED outcome_evaluator — %s: %s", type(exc).__name__, exc)


async def job_tier_scrape(
    scraper: FutGGScraper,
    platform: str,
    rating_min: int,
    rating_max: int,
    limit: int,
    db_path: str,
    tier_name: str,
) -> None:
    """Scrape a rating tier. Circuit-breaks after 5 consecutive failures (pauses 2h)."""
    source = f"futgg_tier_{tier_name}"

    pause_until = _tier_pause_until.get(source)
    if pause_until and datetime.now(timezone.utc) < pause_until:
        logger.info("JOB SKIP   %s — circuit breaker active until %s", source, pause_until.isoformat())
        return

    cons_failures = _get_consecutive_failures(db_path, source)
    if cons_failures >= 5:
        resume_at = datetime.now(timezone.utc) + timedelta(hours=2)
        _tier_pause_until[source] = resume_at
        logger.warning(
            "JOB PAUSE  %s — %d consecutive failures, pausing 2h until %s",
            source, cons_failures, resume_at.isoformat(),
        )
        return

    start = datetime.now(timezone.utc)
    logger.info(
        "JOB START  %s (rating %d-%d, limit=%d, platform=%s)",
        source, rating_min, rating_max, limit, platform,
    )
    try:
        cards = await scraper.fetch_cards_by_rating(rating_min, rating_max, platform, limit=limit)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("JOB DONE   %s — %d cards in %.1fs", source, len(cards), elapsed)
        _tier_pause_until.pop(source, None)
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.error(
            "JOB FAILED %s after %.1fs — %s: %s",
            source, elapsed, type(exc).__name__, exc,
        )


async def job_full_card_sweep(scraper: FutGGScraper, platform: str) -> None:
    """
    Daily full pagination sweep over all rating bands. Discovers every card on
    FUT.GG in the band (the tier scraper only refreshes first-page cards).
    Each band writes its own scraper_health row (futgg_sweep_{min}_{max}_{platform}).
    """
    from src.scrapers.futgg import SWEEP_BANDS

    start = datetime.now(timezone.utc)
    logger.info("JOB START  full_card_sweep_%s (%d bands)", platform, len(SWEEP_BANDS))
    totals = {"found": 0, "new": 0, "updated": 0, "skipped_untradeable": 0}
    for band_min, band_max in SWEEP_BANDS:
        try:
            r = await scraper.fetch_all_cards_paginated(platform, band_min, band_max)  # type: ignore[arg-type]
            for k in totals:
                totals[k] += r.get(k, 0)
            logger.info(
                "  sweep band %d-%d (%s): found=%d new=%d updated=%d skipped_untradeable=%d",
                band_min, band_max, platform,
                r["found"], r["new"], r["updated"], r["skipped_untradeable"],
            )
        except Exception as exc:
            logger.error(
                "  sweep band %d-%d (%s) FAILED — %s: %s",
                band_min, band_max, platform, type(exc).__name__, exc,
            )
        await asyncio.sleep(10)
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info(
        "JOB DONE   full_card_sweep_%s — found=%d new=%d updated=%d skipped_untradeable=%d in %.1fs",
        platform, totals["found"], totals["new"], totals["updated"],
        totals["skipped_untradeable"], elapsed,
    )


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

    scheduler.add_job(
        job_reddit_new,
        trigger=IntervalTrigger(minutes=5, timezone="UTC"),
        args=[db_path],
        id="reddit_new",
        name="Reddit /new posts",
        next_run_time=now + timedelta(seconds=120),
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_reddit_hot,
        trigger=IntervalTrigger(minutes=30, timezone="UTC"),
        args=[db_path],
        id="reddit_hot",
        name="Reddit /hot posts",
        next_run_time=now + timedelta(seconds=150),
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_ea_news,
        trigger=IntervalTrigger(minutes=30, timezone="UTC"),
        args=[db_path],
        id="ea_news",
        name="EA FC news",
        next_run_time=now + timedelta(seconds=180),
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_fodder_sweep,
        trigger=IntervalTrigger(minutes=30, timezone="UTC"),
        args=[scraper, db_path],
        id="fodder_sweep",
        name="Fodder price sweep",
        next_run_time=now + timedelta(seconds=240),
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_signal_tagger,
        trigger=IntervalTrigger(minutes=5, timezone="UTC"),
        args=[db_path],
        id="signal_tagger",
        name="Signal card tagger",
        next_run_time=now + timedelta(seconds=60),
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_recommendations,
        trigger=CronTrigger(hour=8, minute=0, timezone="UTC"),
        args=["pc", db_path],
        id="recommendations_pc",
        name="Autonomous recommendations — PC",
        next_run_time=now + timedelta(seconds=30),
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_outcome_evaluator,
        trigger=IntervalTrigger(hours=6, timezone="UTC"),
        args=[db_path],
        id="outcome_evaluator",
        name="Recommendation outcome evaluator",
        next_run_time=now + timedelta(hours=6),
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_probe_providers,
        trigger=IntervalTrigger(minutes=30, timezone="UTC"),
        id="provider_probe",
        name="LLM provider health probe",
        next_run_time=now + timedelta(seconds=20),
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    # --- Card coverage tier jobs (PC-primary; staggered to avoid hammering FUT.GG) ---
    # Tier 1: 85-91 rated, top 200, every 30 min — primary trading targets
    scheduler.add_job(
        job_tier_scrape,
        trigger=IntervalTrigger(minutes=30, timezone="UTC"),
        args=[scraper, "pc", 85, 91, 200, db_path, "t1_pc"],
        id="tier1_pc",
        name="Card coverage Tier 1 (85-91) — PC",
        next_run_time=now + timedelta(seconds=300),
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        job_tier_scrape,
        trigger=IntervalTrigger(minutes=30, timezone="UTC"),
        args=[scraper, "console", 85, 91, 200, db_path, "t1_console"],
        id="tier1_console",
        name="Card coverage Tier 1 (85-91) — Console",
        next_run_time=now + timedelta(seconds=600),
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    # Tier 2: 82-84 rated, top 100, every 60 min — fodder range
    scheduler.add_job(
        job_tier_scrape,
        trigger=IntervalTrigger(minutes=60, timezone="UTC"),
        args=[scraper, "pc", 82, 84, 100, db_path, "t2_pc"],
        id="tier2_pc",
        name="Card coverage Tier 2 (82-84) — PC",
        next_run_time=now + timedelta(seconds=900),
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    # Tier 3: 92+ rated, top 50, every 60 min — icons / highest TOTS
    scheduler.add_job(
        job_tier_scrape,
        trigger=IntervalTrigger(minutes=60, timezone="UTC"),
        args=[scraper, "pc", 92, 99, 50, db_path, "t3_pc"],
        id="tier3_pc",
        name="Card coverage Tier 3 (92+) — PC",
        next_run_time=now + timedelta(seconds=1200),
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )

    # Daily full card sweep — paginates every rating band, both platforms
    scheduler.add_job(
        job_full_card_sweep,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        args=[scraper, "pc"],
        id="full_card_sweep_pc",
        name="Full card sweep (all bands) — PC",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        job_full_card_sweep,
        trigger=CronTrigger(hour=6, minute=30, timezone="UTC"),
        args=[scraper, "console"],
        id="full_card_sweep_console",
        name="Full card sweep (all bands) — Console",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    # Tier 4: 78-81 rated, top 50, every 4 hours — cheap SBC fodder
    scheduler.add_job(
        job_tier_scrape,
        trigger=IntervalTrigger(hours=4, timezone="UTC"),
        args=[scraper, "pc", 78, 81, 50, db_path, "t4_pc"],
        id="tier4_pc",
        name="Card coverage Tier 4 (78-81) — PC",
        next_run_time=now + timedelta(seconds=1500),
        misfire_grace_time=1800,
        coalesce=True,
        max_instances=1,
    )

    return scheduler


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _http_trigger_server(db_path: str, scraper: FutGGScraper | None = None) -> None:
    """Minimal asyncio HTTP server on port 8765 for UI-triggered recommendation runs
    and on-demand card re-scrapes (POST /fetch-card)."""

    async def _run_recs_with_result(platform: str, db_path: str, provider_id: str = "haiku") -> dict:
        """Run recommendations, returning {status, skipped, reason, recs_added}."""
        try:
            con = sqlite3.connect(db_path)
            candidates = _get_candidates(con, platform)
            con.close()

            should_run, reason = _has_worthy_candidates(candidates, platform, db_path)
            if not should_run:
                logger.info("Manual trigger skip (%s): %s", platform, reason)
                return {"status": "ok", "skipped": True, "reason": reason, "recs_added": 0}

            recs = await run_recommendations_safe(platform, db_path, provider_id)
            if recs is None:
                logger.info("Manual trigger skip (%s): run already in progress", platform)
                return {
                    "status": "skipped", "skipped": True,
                    "reason": "recommendation run already in progress", "recs_added": 0,
                }
            logger.info("Manual trigger done: %d recs for %s via %s", len(recs), platform, provider_id)
            return {"status": "ok", "skipped": False, "reason": "", "recs_added": len(recs)}
        except Exception as exc:
            logger.error("Manual trigger failed for %s: %s", platform, exc)
            return {"status": "error", "error": str(exc), "skipped": False, "recs_added": 0}

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        import json as _json
        try:
            data = await asyncio.wait_for(reader.read(2048), timeout=5.0)
            text = data.decode("utf-8", errors="replace")
            first_line = text.split("\r\n")[0]
            if "POST /run-recommendations" in first_line:
                body_start = data.find(b"\r\n\r\n")
                body = data[body_start + 4:].decode("utf-8", errors="replace") if body_start >= 0 else ""
                platform = "pc"
                provider_id = "haiku"
                try:
                    payload = _json.loads(body)
                    platform = payload.get("platform", "pc")
                    provider_id = payload.get("provider_id", "haiku")
                except Exception:
                    pass
                logger.info("HTTP trigger: run-recommendations for %s via %s", platform, provider_id)
                result = await _run_recs_with_result(platform, db_path, provider_id)
                resp_body = _json.dumps(result).encode("utf-8")
                header = (
                    f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    f"Access-Control-Allow-Origin: *\r\nContent-Length: {len(resp_body)}\r\n\r\n"
                ).encode("utf-8")
                resp = header + resp_body
            elif "POST /fetch-card" in first_line:
                body_start = data.find(b"\r\n\r\n")
                body = data[body_start + 4:].decode("utf-8", errors="replace") if body_start >= 0 else ""
                result = {"status": "error", "error": "bad request"}
                try:
                    payload = _json.loads(body)
                    platform = payload.get("platform", "pc")
                    card_key = payload.get("card_key")
                    if not card_key and payload.get("card_id") is not None:
                        con = sqlite3.connect(db_path)
                        row = con.execute(
                            "SELECT card_key FROM cards WHERE id=?", (payload["card_id"],)
                        ).fetchone()
                        con.close()
                        card_key = row[0] if row else None
                    if not card_key:
                        result = {"status": "error", "error": "card not found"}
                    elif scraper is None:
                        result = {"status": "error", "error": "scraper not available"}
                    else:
                        # Fire-and-forget: the scrape takes ~10s; don't block the UI.
                        logger.info("HTTP trigger: fetch-card %s (%s)", card_key, platform)
                        asyncio.create_task(scraper.fetch_card_prices(card_key, platform))
                        result = {"status": "queued", "card_key": card_key, "platform": platform}
                except Exception as exc2:
                    result = {"status": "error", "error": str(exc2)}
                resp_body = _json.dumps(result).encode("utf-8")
                header = (
                    f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    f"Access-Control-Allow-Origin: *\r\nContent-Length: {len(resp_body)}\r\n\r\n"
                ).encode("utf-8")
                resp = header + resp_body
            elif "POST /probe-providers" in first_line:
                # Serve the 30-min cache; probe inline only when the cache is
                # still empty (endpoint hit before the first scheduled probe).
                if not _provider_health:
                    await job_probe_providers()
                resp_body = _json.dumps(_provider_health).encode("utf-8")
                header = (
                    f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    f"Access-Control-Allow-Origin: *\r\nContent-Length: {len(resp_body)}\r\n\r\n"
                ).encode("utf-8")
                resp = header + resp_body
            else:
                resp = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
            writer.write(resp)
            await writer.drain()
        except Exception as exc:
            logger.debug("HTTP server error: %s", exc)
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(_handle, "127.0.0.1", 8765)
        logger.info("HTTP trigger server listening on 127.0.0.1:8765")
        async with server:
            await server.serve_forever()
    except OSError as exc:
        # Port 8765 is a hard singleton: a second scheduler must die loudly, not
        # silently serve stale code without a trigger server (Session 33 root cause 2).
        logger.error(
            "FATAL: port 8765 already in use — another scheduler is running. "
            "Kill all python.exe processes and restart. Exiting. (%s)", exc
        )
        try:
            con = sqlite3.connect(db_path)
            con.execute(
                "INSERT INTO scraper_health (source, run_at_utc, success, last_error) "
                "VALUES (?, ?, 0, ?)",
                (
                    "scheduler",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "Port 8765 already in use — stale orphan detected",
                ),
            )
            con.commit()
            con.close()
        except Exception as db_exc:
            logger.error("Could not write scraper_health row: %s", db_exc)
        logging.shutdown()
        # os._exit: we are inside an asyncio task; sys.exit would only kill the task.
        __import__("os")._exit(1)
    except Exception as exc:
        logger.error("HTTP trigger server failed to start: %s", exc)


async def run(db_path: str = _DB_PATH) -> None:
    """Start the scheduler and block until SIGINT/SIGTERM."""
    setup_logging()
    logger.info("FCPriceMaster scheduler starting (pid=%d)", __import__("os").getpid())

    run_migrations(db_path)

    scraper = FutGGScraper(db_path=db_path)
    await scraper.__aenter__()

    scheduler = build_scheduler(scraper, db_path)
    scheduler.start()
    asyncio.create_task(_http_trigger_server(db_path, scraper))

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
