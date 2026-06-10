"""
Build structured context from the DB for LLM trade-call analysis.

Given raw text + platform, returns a dict with:
  - mentioned_cards: matched cards with price history
  - fodder_context: current fodder prices for mentioned ratings
  - release_calendar: promo proximity
  - recent_signals: last 10 signals mentioning the same cards
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_DB_PATH = str(Path(__file__).parents[3] / "data" / "fcpricemaster.db")
_CALENDAR_PATH = Path(__file__).parents[3] / "config" / "release_calendar.yaml"


def _db_path_from_env() -> str:
    return os.environ.get("DB_PATH", _DB_PATH)


# ---------------------------------------------------------------------------
# Card matching
# ---------------------------------------------------------------------------

def _match_cards(con: sqlite3.Connection, text: str) -> list[dict[str, Any]]:
    """Return cards mentioned in text using aliases + fallback name substring."""
    text_lower = text.lower()
    matched: dict[int, dict[str, Any]] = {}

    # Check card_aliases first (exact alias match)
    aliases = con.execute(
        "SELECT alias, card_id FROM card_aliases"
    ).fetchall()
    for alias, card_id in aliases:
        if alias.lower() in text_lower and card_id not in matched:
            row = con.execute(
                "SELECT id, card_key, player_name, version_name FROM cards WHERE id=?",
                (card_id,),
            ).fetchone()
            if row:
                matched[card_id] = dict(zip(["id", "card_key", "player_name", "version_name"], row))

    # Fallback: substring match on player_name (full name or any significant part)
    cards = con.execute(
        "SELECT id, card_key, player_name, version_name FROM cards"
    ).fetchall()
    for card_id, card_key, player_name, version_name in cards:
        if card_id in matched:
            continue
        name_lower = player_name.lower()
        # Full name match
        if len(name_lower) >= 4 and name_lower in text_lower:
            matched[card_id] = {
                "id": card_id, "card_key": card_key,
                "player_name": player_name, "version_name": version_name,
            }
            continue
        # Individual word match (last name or first name, ≥5 chars to avoid collisions)
        parts = name_lower.split()
        for part in parts:
            if len(part) >= 5 and part in text_lower:
                matched[card_id] = {
                    "id": card_id, "card_key": card_key,
                    "player_name": player_name, "version_name": version_name,
                }
                break

    return list(matched.values())


# ---------------------------------------------------------------------------
# Price context
# ---------------------------------------------------------------------------

def _price_context(con: sqlite3.Connection, card_id: int, platform: str) -> dict[str, Any]:
    """Return current price, 24h ago price, 7d history summary."""
    latest = con.execute(
        """SELECT bin_price, ts_utc FROM price_snapshots
           WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
           ORDER BY ts_utc DESC LIMIT 1""",
        (card_id, platform),
    ).fetchone()
    prev_24h = con.execute(
        """SELECT bin_price FROM price_snapshots
           WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
             AND ts_utc <= datetime('now', '-24 hours')
           ORDER BY ts_utc DESC LIMIT 1""",
        (card_id, platform),
    ).fetchone()
    week = con.execute(
        """SELECT MIN(bin_price), MAX(bin_price), COUNT(*) FROM price_snapshots
           WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
             AND ts_utc >= datetime('now', '-7 days')""",
        (card_id, platform),
    ).fetchone()

    current = latest[0] if latest else None
    last_ts = latest[1] if latest else None
    ago_24h = prev_24h[0] if prev_24h else None
    change_24h = None
    if current and ago_24h and ago_24h > 0:
        change_24h = round((current - ago_24h) / ago_24h * 100, 1)

    # Data age + volume: the LLM must know how old the latest price is.
    data_age_hours = None
    if last_ts:
        try:
            ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:  # naive timestamps in the DB are UTC by convention
                ts = ts.replace(tzinfo=timezone.utc)
            data_age_hours = round((datetime.now(timezone.utc) - ts).total_seconds() / 3600, 1)
        except ValueError:
            pass
    snapshot_count = con.execute(
        """SELECT COUNT(*) FROM price_snapshots
           WHERE card_id=? AND platform=? AND bin_price > 0""",
        (card_id, platform),
    ).fetchone()[0]

    trend = "unknown"
    if change_24h is not None:
        if change_24h > 5:
            trend = "rising"
        elif change_24h < -5:
            trend = "falling"
        else:
            trend = "stable"

    return {
        "current_price": current,
        "price_24h_ago": ago_24h,
        "change_24h_pct": change_24h,
        "week_low": week[0] if week else None,
        "week_high": week[1] if week else None,
        "trend": trend,
        "last_snapshot_ts": last_ts,
        "data_age_hours": data_age_hours,
        "snapshot_count": snapshot_count,
    }


# ---------------------------------------------------------------------------
# Fodder context
# ---------------------------------------------------------------------------

def _fodder_context(con: sqlite3.Connection, text: str, platform: str) -> list[dict[str, Any]]:
    """Return fodder snapshots (with top-card details) for any ratings 81-93 mentioned in text."""
    import re
    rating_matches = re.findall(r"(?<!\d)(8[1-9]|9[0-3])(?!\d)", text)
    ratings = list({int(r) for r in rating_matches})
    results = []
    for rating in sorted(ratings):
        snap = con.execute(
            """SELECT id, cheapest_bin, second_cheapest_bin, median_bin, ts_utc
               FROM fodder_snapshots
               WHERE rating=? AND platform=? ORDER BY ts_utc DESC LIMIT 1""",
            (rating, platform),
        ).fetchone()
        if not snap:
            continue
        snap_id, cheapest_bin, second_cheapest_bin, median_bin, last_updated = snap
        cards = con.execute(
            """SELECT player_name, position, club_name, nation_name, card_version, bin_price, rank_in_rating
               FROM fodder_cards
               WHERE snapshot_id=? ORDER BY rank_in_rating ASC LIMIT 10""",
            (snap_id,),
        ).fetchall()
        results.append({
            "rating": rating,
            "cheapest_bin": cheapest_bin,
            "second_cheapest_bin": second_cheapest_bin,
            "median_bin": median_bin,
            "last_updated": last_updated,
            "top_cards": [
                {
                    "rank": r[6], "player_name": r[0], "position": r[1],
                    "club": r[2], "nation": r[3], "version": r[4], "price": r[5],
                }
                for r in cards
            ],
        })
    return results


# ---------------------------------------------------------------------------
# Release calendar
# ---------------------------------------------------------------------------

def _calendar_context() -> dict[str, Any]:
    """Return promo proximity and end-of-cycle context from release_calendar.yaml."""
    from datetime import date as _date

    try:
        with open(_CALENDAR_PATH, encoding="utf-8") as f:
            cal = yaml.safe_load(f)
    except OSError:
        return {
            "today": datetime.now(timezone.utc).date().isoformat(),
            "days_to_next_launch": None,
            "end_of_cycle_phase": "none",
            "futties_active": False,
            "futties_days_until": None,
            "promos": [],
        }

    today = datetime.now(timezone.utc).date()

    # --- next game launch ---
    days_to_next_launch: int | None = None
    end_of_cycle_phase = "none"
    game_cycle = cal.get("game_cycle", {})
    launch_str = game_cycle.get("next_game_launch", "")
    if launch_str:
        try:
            launch_date = _date.fromisoformat(str(launch_str))
            days_to_next_launch = (launch_date - today).days
            if days_to_next_launch <= 0:
                end_of_cycle_phase = "late"
            elif days_to_next_launch < 30:
                end_of_cycle_phase = "late"
            elif days_to_next_launch < 60:
                end_of_cycle_phase = "mid"
            elif days_to_next_launch < 120:
                end_of_cycle_phase = "early"
            else:
                end_of_cycle_phase = "none"
        except (ValueError, TypeError):
            pass

    # --- FUTTIES window ---
    futties_active = False
    futties_days_until: int | None = None
    eoc = cal.get("end_of_cycle", {})
    futties_start_mmdd = eoc.get("futties_window_start", "")
    futties_end_mmdd = eoc.get("futties_window_end", "")
    if futties_start_mmdd and futties_end_mmdd:
        try:
            year = today.year
            fs = _date(year, int(futties_start_mmdd[:2]), int(futties_start_mmdd[3:]))
            fe = _date(year, int(futties_end_mmdd[:2]), int(futties_end_mmdd[3:]))
            if fe < today:
                fs = _date(year + 1, int(futties_start_mmdd[:2]), int(futties_start_mmdd[3:]))
                fe = _date(year + 1, int(futties_end_mmdd[:2]), int(futties_end_mmdd[3:]))
            futties_active = fs <= today <= fe
            futties_days_until = (fs - today).days if not futties_active else 0
        except (ValueError, TypeError):
            pass

    # --- promo list ---
    promo_context = []
    for promo in cal.get("promos", []):
        window_start = promo.get("window_start", "")
        window_end = promo.get("window_end", "")
        name = promo.get("name", "")
        category = promo.get("category", "")
        if not window_start:
            continue
        year = today.year
        try:
            start = _date(year, int(window_start[:2]), int(window_start[3:]))
            end = _date(year, int(window_end[:2]), int(window_end[3:]))
            if start < today and end < today:
                start = _date(year + 1, int(window_start[:2]), int(window_start[3:]))
                end = _date(year + 1, int(window_end[:2]), int(window_end[3:]))
            days_until = (start - today).days
            in_window = start <= today <= end
            promo_context.append({
                "name": name,
                "category": category,
                "days_until_start": days_until if days_until >= 0 else None,
                "in_window": in_window,
                "window_start": str(start),
                "window_end": str(end),
            })
        except (ValueError, TypeError):
            continue

    # FUTTIES and end-of-cycle promos: always include when ≤90 days out or active.
    # Regular promos: include when ≤21 days.
    upcoming = []
    for p in promo_context:
        d = p.get("days_until_start")
        is_eoc = p.get("category") == "end_of_cycle"
        if p["in_window"] or (d is not None and d <= (90 if is_eoc else 21)):
            upcoming.append(p)

    return {
        "today": today.isoformat(),
        "days_to_next_launch": days_to_next_launch,
        "end_of_cycle_phase": end_of_cycle_phase,
        "futties_active": futties_active,
        "futties_days_until": futties_days_until,
        "promos": sorted(upcoming, key=lambda p: (p["days_until_start"] or 0))[:5],
    }


# ---------------------------------------------------------------------------
# Recent signals
# ---------------------------------------------------------------------------

def _recent_signals(con: sqlite3.Connection, card_ids: list[int]) -> list[dict[str, Any]]:
    """Return last 10 signals mentioning any of the given card IDs."""
    if not card_ids:
        rows = con.execute(
            """SELECT raw_text, source, source_server, ts_utc,
                      COALESCE(signal_context, 'fut_market') AS signal_context
               FROM signals WHERE raw_text IS NOT NULL
               ORDER BY ts_utc DESC LIMIT 10"""
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(card_ids))
        rows = con.execute(
            f"""SELECT DISTINCT s.raw_text, s.source, s.source_server, s.ts_utc,
                       COALESCE(s.signal_context, 'fut_market') AS signal_context
                FROM signals s
                JOIN signal_card_tags t ON t.signal_id = s.id
                WHERE t.card_id IN ({placeholders}) AND s.raw_text IS NOT NULL
                ORDER BY s.ts_utc DESC LIMIT 10""",
            card_ids,
        ).fetchall()
    return [
        {"text": r[0], "source": r[1], "server": r[2], "ts_utc": r[3], "signal_context": r[4]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_context(text: str, platform: str, db_path: str | None = None) -> dict[str, Any]:
    """Build full context for the LLM from the DB."""
    path = db_path or _db_path_from_env()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row

    try:
        matched = _match_cards(con, text)
        card_ids = [c["id"] for c in matched]

        # Enrich each card with price data
        enriched_cards = []
        for card in matched:
            attrs = con.execute(
                "SELECT key, value FROM card_attributes WHERE card_id=?", (card["id"],)
            ).fetchall()
            attr_dict = {r[0]: r[1] for r in attrs}
            prices = _price_context(con, card["id"], platform)
            enriched_cards.append({**card, **attr_dict, **prices})

        cal = _calendar_context()
        return {
            "mentioned_cards": enriched_cards,
            "fodder_context": _fodder_context(con, text, platform),
            "release_calendar": cal,
            "recent_signals": _recent_signals(con, card_ids),
            "platform": platform,
            # top-level shortcuts for easy consumption in format helpers
            "days_to_next_launch": cal.get("days_to_next_launch"),
            "end_of_cycle_phase": cal.get("end_of_cycle_phase", "none"),
            "futties_active": cal.get("futties_active", False),
            "futties_days_until": cal.get("futties_days_until"),
        }
    finally:
        con.close()
