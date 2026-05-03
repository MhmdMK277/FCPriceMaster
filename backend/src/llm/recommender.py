"""
FCPriceMaster autonomous recommendation engine.

Runs every 2h via scheduler. Scans candidate cards, calls Claude for each,
stores structured buy/avoid recommendations in the DB.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.llm.context_builder import build_context, _calendar_context
from src.llm.ask import (
    _load_api_key,
    _load_config,
    _check_daily_cap,
    _log_call,
    _INPUT_COST_PER_TOKEN,
    _OUTPUT_COST_PER_TOKEN,
)

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_AUTONOMOUS_SYSTEM_PROMPT = """You are FCPriceMaster, an EA FC Ultimate Team market analyst running autonomous market surveillance. You have been given data about a specific card and must decide whether it represents a trading opportunity RIGHT NOW.

You are NOT evaluating a human's trade call. You are originating one.
Be selective — only flag genuine opportunities. If nothing stands out, say so.

Context includes: current price, 24h momentum, 7-day range, recent signals mentioning this card, upcoming promo calendar, and fodder context if relevant.

Decision criteria:
BUY: Price is at or near a local low, upcoming demand catalyst exists (SBC, promo, TOTW, fixture), risk/reward favors entry
AVOID: Price is elevated above baseline due to hype or recent spike, likely to correct; or no clear catalyst for appreciation
If neither clearly applies: respond with verdict="hold" and low confidence

End-of-cycle awareness (apply based on days_to_next_launch in the user message):
- days_to_next_launch > 120: do NOT mention the next game. Irrelevant.
- 60-120 days: for LONG horizon only, briefly note end-of-cycle selling pressure may build.
- 30-60 days: factor it for medium and long trades. Market weakens progressively.
- < 30 days: factor it for ALL trades. Almost nothing is worth buying except specific end-of-cycle plays.
Always refer to "next game launch" generically — never hardcode a game name.

FUTTIES rules (apply only when futties_active=True in the user message):
- 85-rated gold cards: STRONG BUY signal due to repeatable 85x10 SBC demand. State this explicitly.
- All other cards: heavy AVOID bias. Repeatable pack supply floods the market relentlessly.
If futties_days_until < 30: warn about approaching FUTTIES for any long-horizon recommendations.

Respond in this exact JSON format:
{
  "verdict": "buy" | "avoid" | "hold",
  "confidence": 0-100,
  "reasoning": "2-3 sentence explanation of the specific opportunity or risk",
  "price_context": "what current price tells us vs baseline",
  "risk": "low" | "medium" | "high",
  "suggested_buy_price": null or number,
  "suggested_sell_price": null or number,
  "horizon": "short (hours)" | "medium (days)" | "long (weeks)"
}
Respond ONLY with the JSON object. No preamble, no markdown fences."""

_FODDER_SYSTEM_PROMPT = """You are FCPriceMaster. You are evaluating whether a specific fodder rating is a BUY opportunity right now based on current cheapest BIN prices, 7-day price history, and upcoming SBC/promo calendar.

Fodder BUY criteria: price near 7-day low AND an SBC or promo expected within 14 days that will consume this rating.

Respond in this exact JSON format:
{
  "verdict": "buy" | "avoid" | "hold",
  "confidence": 0-100,
  "reasoning": "2-3 sentence explanation",
  "price_context": "current price vs 7-day range",
  "risk": "low" | "medium" | "high",
  "suggested_buy_price": null or number,
  "suggested_sell_price": null or number,
  "horizon": "short (hours)" | "medium (days)" | "long (weeks)"
}
Respond ONLY with the JSON object. No preamble, no markdown fences."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_horizon(horizon: str) -> int:
    h = horizon.lower()
    if "short" in h:
        return 12
    if "medium" in h:
        return 72
    if "long" in h:
        return 168
    return 48


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        return "\n".join(lines[start:end]).strip()
    return text


def _get_candidates(con: sqlite3.Connection, platform: str, limit: int = 20) -> list[dict[str, Any]]:
    """
    3-pool candidate selection (tradeable=1 cards only):
      Pool A — cards mentioned in signals in the last 48h (up to 8)
      Pool B — cards at or within 10% of their 7-day low (up to 8)
      Pool C — trending cards with 3+ snapshots in 48h (fallback, up to remaining slots)
    """
    def _fetch_pool(sql: str, params: tuple, pool_limit: int, label: str) -> list[dict[str, Any]]:
        rows = con.execute(sql, params).fetchall()
        results = []
        for r in rows[:pool_limit]:
            results.append({
                "card_id": r[0], "player_name": r[1], "version_name": r[2],
                "card_key": r[3], "signal_count_24h": r[4], "_pool": label,
            })
        return results

    pool_a = _fetch_pool(
        """
        SELECT c.id, c.player_name, c.version_name, c.card_key,
               COUNT(s.id) AS signal_count_24h
        FROM cards c
        JOIN signal_card_tags sct ON sct.card_id = c.id
        JOIN signals s ON s.id = sct.signal_id
        WHERE s.ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-48 hours')
          AND COALESCE(c.tradeable, 1) = 1
        GROUP BY c.id
        ORDER BY COUNT(s.id) DESC
        """,
        (),
        8,
        "signal",
    )

    seen: set[int] = {c["card_id"] for c in pool_a}

    pool_b_rows = con.execute(
        """
        SELECT card_id, platform,
               MIN(bin_price) AS week_low,
               (SELECT bin_price FROM price_snapshots p2
                WHERE p2.card_id=p.card_id AND p2.platform=p.platform
                ORDER BY ts_utc DESC LIMIT 1) AS current_price
        FROM price_snapshots p
        WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')
          AND platform=?
          AND bin_price > 0
        GROUP BY card_id, platform
        HAVING current_price <= week_low * 1.10
        ORDER BY (current_price * 1.0 / week_low) ASC
        """,
        (platform,),
    ).fetchall()

    pool_b: list[dict[str, Any]] = []
    for r in pool_b_rows:
        cid = r[0]
        if cid in seen or len(pool_b) >= 8:
            continue
        card = con.execute(
            "SELECT id, player_name, version_name, card_key FROM cards WHERE id=? AND COALESCE(tradeable, 1) = 1",
            (cid,),
        ).fetchone()
        if not card:
            continue
        sig_count = con.execute(
            """SELECT COUNT(*) FROM signal_card_tags t JOIN signals s ON s.id=t.signal_id
               WHERE t.card_id=? AND s.ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-24 hours')""",
            (cid,),
        ).fetchone()[0]
        pool_b.append({
            "card_id": card[0], "player_name": card[1], "version_name": card[2],
            "card_key": card[3], "signal_count_24h": sig_count, "_pool": "7d_low",
        })
        seen.add(cid)

    remaining = limit - len(pool_a) - len(pool_b)
    pool_c: list[dict[str, Any]] = []
    if remaining > 0:
        pool_c = _fetch_pool(
            """
            WITH eligible AS (
                SELECT card_id, COUNT(*) AS snap_count
                FROM price_snapshots
                WHERE platform=? AND ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-48 hours')
                  AND bin_price IS NOT NULL
                GROUP BY card_id
                HAVING COUNT(*) >= 3
            ),
            sig_counts AS (
                SELECT t.card_id, COUNT(*) AS sig_count
                FROM signal_card_tags t
                JOIN signals s ON s.id = t.signal_id
                WHERE s.ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-24 hours')
                GROUP BY t.card_id
            )
            SELECT e.card_id, c.player_name, c.version_name, c.card_key,
                   COALESCE(sc.sig_count, 0) AS signal_count_24h
            FROM eligible e
            JOIN cards c ON c.id = e.card_id
            LEFT JOIN sig_counts sc ON sc.card_id = e.card_id
            WHERE e.card_id NOT IN ({placeholders})
              AND COALESCE(c.tradeable, 1) = 1
            ORDER BY COALESCE(sc.sig_count, 0) DESC
            LIMIT ?
            """.replace("{placeholders}", ",".join("?" * len(seen)) if seen else "SELECT -1"),
            (platform, *seen, remaining) if seen else (platform, remaining),
            remaining,
            "trending",
        )

    # Pool D — FUTTIES special: 85-rated cards only active during FUTTIES window
    pool_d: list[dict[str, Any]] = []
    cal = _calendar_context()
    if cal.get("futties_active"):
        pool_d = _fetch_pool(
            """
            SELECT c.id, c.player_name, c.version_name, c.card_key,
                   COALESCE(sc.sig_count, 0) AS signal_count_24h
            FROM cards c
            JOIN card_attributes ca ON ca.card_id = c.id AND ca.key = 'rating' AND ca.value = '85'
            LEFT JOIN (
                SELECT t.card_id, COUNT(*) AS sig_count
                FROM signal_card_tags t
                JOIN signals s ON s.id = t.signal_id
                WHERE s.ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-48 hours')
                GROUP BY t.card_id
            ) sc ON sc.card_id = c.id
            WHERE c.id IN (
                SELECT DISTINCT card_id FROM price_snapshots
                WHERE platform=? AND ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')
            )
            AND COALESCE(c.tradeable, 1) = 1
            AND c.id NOT IN ({placeholders})
            ORDER BY COALESCE(sc.sig_count, 0) DESC
            LIMIT 8
            """.replace("{placeholders}", ",".join("?" * len(seen)) if seen else "SELECT -1"),
            (platform, *seen) if seen else (platform,),
            8,
            "futties_85",
        )

    return pool_a + pool_b + pool_c + pool_d


def _get_price_momentum(con: sqlite3.Connection, card_id: int, platform: str) -> dict[str, Any]:
    latest = con.execute(
        """SELECT bin_price FROM price_snapshots
           WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
           ORDER BY ts_utc DESC LIMIT 1""",
        (card_id, platform),
    ).fetchone()
    prev = con.execute(
        """SELECT bin_price FROM price_snapshots
           WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
             AND ts_utc <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-24 hours')
           ORDER BY ts_utc DESC LIMIT 1""",
        (card_id, platform),
    ).fetchone()
    price_now = latest[0] if latest else None
    price_24h = prev[0] if prev else None
    momentum = None
    if price_now and price_24h and price_24h > 0:
        momentum = round((price_now - price_24h) / price_24h * 100, 1)
    return {"price_now": price_now, "price_24h": price_24h, "momentum_score": momentum}


def _has_recent_rec(
    con: sqlite3.Connection,
    card_id: int | None,
    platform: str,
    hours: int = 6,
    player_name: str | None = None,
) -> bool:
    if card_id is None and player_name is None:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if player_name:
        # Guard by player name so different card versions of the same player are deduped
        row = con.execute(
            """SELECT r.id FROM recommendations r
               JOIN cards c ON c.id = r.card_id
               WHERE c.player_name=? AND r.platform=? AND r.dismissed_at IS NULL
                 AND r.ts_utc >= ?""",
            (player_name, platform, cutoff),
        ).fetchone()
    else:
        row = con.execute(
            """SELECT id FROM recommendations
               WHERE card_id=? AND platform=? AND dismissed_at IS NULL
                 AND ts_utc >= ?""",
            (card_id, platform, cutoff),
        ).fetchone()
    return row is not None


def _days_since_last_rec(con: sqlite3.Connection, card_id: int, platform: str) -> int | None:
    row = con.execute(
        """SELECT ts_utc FROM recommendations
           WHERE card_id=? AND platform=?
           ORDER BY ts_utc DESC LIMIT 1""",
        (card_id, platform),
    ).fetchone()
    if not row:
        return None
    try:
        ts = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        return delta.days
    except Exception:
        return None


def _format_card_message(card: dict[str, Any], context: dict[str, Any], momentum: dict[str, Any]) -> str:
    lines: list[str] = []
    cards = context.get("mentioned_cards", [])
    c = cards[0] if cards else {}

    price_now = momentum.get("price_now")
    price_24h = momentum.get("price_24h")
    mom = momentum.get("momentum_score")

    lines.append(f"Card: {card['player_name']} ({card['version_name']})")
    lines.append(f"Platform: {context.get('platform', '?').upper()}")
    lines.append("")
    lines.append(f"Current price: {price_now:,}" if price_now else "Current price: N/A")
    lines.append(f"24h ago: {price_24h:,}" if price_24h else "24h ago: N/A")
    lines.append(f"Momentum: {mom:+.1f}%" if mom is not None else "Momentum: N/A")
    if c:
        wl = c.get("week_low")
        wh = c.get("week_high")
        lines.append(f"7d range: {wl or 'N/A'} – {wh or 'N/A'}")
    lines.append(f"Signals mentioning card (last 24h): {card['signal_count_24h']}")

    days = _days_since_last_rec_from_card(card)
    if days is not None:
        lines.append(f"Days since last recommendation: {days}")

    # End-of-cycle context
    days_launch = context.get("days_to_next_launch")
    eoc_phase = context.get("end_of_cycle_phase", "none")
    futties_active = context.get("futties_active", False)
    futties_until = context.get("futties_days_until")
    if days_launch is not None:
        lines.append(f"days_to_next_launch: {days_launch}")
    if eoc_phase != "none":
        lines.append(f"end_of_cycle_phase: {eoc_phase}")
    lines.append(f"futties_active: {futties_active}")
    if futties_until is not None and not futties_active:
        lines.append(f"futties_days_until: {futties_until}")

    cal = context.get("release_calendar", {})
    promos = cal.get("promos", [])
    if promos:
        lines.append("")
        lines.append("Upcoming promos:")
        for p in promos:
            if p.get("in_window"):
                lines.append(f"  ACTIVE: {p['name']} (ends {p.get('window_end', '?')})")
            elif p.get("days_until_start") is not None:
                lines.append(f"  {p['name']} in {p['days_until_start']} days")

    signals = context.get("recent_signals", [])
    if signals:
        lines.append("")
        lines.append("Recent signals:")
        for s in signals[:5]:
            txt = (s.get("text") or "")[:100]
            lines.append(f"  [{s.get('source', '?')}] {txt}")

    return "\n".join(lines)


def _days_since_last_rec_from_card(card: dict[str, Any]) -> int | None:
    return card.get("_days_since_rec")


def _has_worthy_candidates(
    candidates: list[dict[str, Any]],
    platform: str,
    db_path: str,
) -> tuple[bool, str]:
    """
    Return (should_run, reason) before spending any API budget.
    Called inside generate_recommendations before any LLM call.
    Also used by the HTTP trigger handler to return early skip reasons to the UI.
    """
    if not candidates:
        return False, "No candidate cards found with sufficient price history."

    con = sqlite3.connect(db_path)
    try:
        recent_count = sum(
            1 for c in candidates
            if _has_recent_rec(con, c["card_id"], platform, hours=10, player_name=c["player_name"])
        )
        if recent_count == len(candidates):
            return False, f"All {len(candidates)} candidates already have recent recommendations."

        sig_count = con.execute(
            "SELECT COUNT(*) FROM signals WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-6 hours')"
        ).fetchone()[0]
    finally:
        con.close()

    if sig_count == 0 and len(candidates) < 3:
        return False, "No recent signals and insufficient candidate pool."

    return True, f"{len(candidates)} candidates ready, {sig_count} recent signals."


def _get_budget_status(db_path: str) -> dict[str, Any]:
    """Return autonomous budget status dict for the UI."""
    spent = _check_autonomous_budget(db_path)
    cap = 0.02
    return {
        "spent_today_usd": round(spent, 6),
        "cap_usd": cap,
        "remaining_usd": round(max(0.0, cap - spent), 6),
        "can_generate": spent <= cap,
    }


def _check_autonomous_budget(db_path: str, limit_usd: float = 0.02) -> float:
    """Return today's autonomous spend in USD. Returns the amount spent."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE ts_utc >= ? AND feature='autonomous'",
            (today + "T00:00:00Z",),
        ).fetchone()
        con.close()
        return float(row[0]) if row else 0.0
    except Exception as exc:
        logger.warning("Could not check autonomous budget: %s", exc)
        return 0.0


def _call_llm(
    client: Any,
    model: str,
    max_tokens: int,
    system: str,
    user_message: str,
) -> tuple[dict[str, Any], int, int]:
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = _strip_fences(response.content[0].text.strip())
    verdict = json.loads(raw)
    return verdict, response.usage.input_tokens, response.usage.output_tokens


def _insert_recommendation(
    con: sqlite3.Connection,
    card_id: int | None,
    platform: str,
    verdict: dict[str, Any],
    source: str = "llm_autonomous",
) -> dict[str, Any]:
    call = verdict["verdict"]
    confidence = float(verdict["confidence"])
    horizon = _parse_horizon(verdict.get("horizon", "medium (days)"))
    target_price = verdict.get("suggested_buy_price") or verdict.get("suggested_sell_price")
    reasoning = verdict.get("reasoning", "")

    cur = con.execute(
        """INSERT INTO recommendations (card_id, platform, call, confidence, horizon_hours, target_price, reasoning, source)
           VALUES (?,?,?,?,?,?,?,?)""",
        (card_id, platform, call, confidence, horizon, target_price, reasoning, source),
    )
    con.commit()
    return {
        "id": cur.lastrowid,
        "card_id": card_id,
        "platform": platform,
        "call": call,
        "confidence": confidence,
        "horizon_hours": horizon,
        "target_price": target_price,
        "reasoning": reasoning,
        "source": source,
        "verdict_full": verdict,
    }


# ---------------------------------------------------------------------------
# FUTTIES structural rec (85-rated fodder)
# ---------------------------------------------------------------------------

def _futties_85_recommendation(con: sqlite3.Connection, platform: str) -> dict[str, Any] | None:
    """Emit a structural BUY for 85-rated fodder when FUTTIES is active. No LLM call — structural demand."""
    existing = con.execute(
        """SELECT id FROM recommendations
           WHERE card_id IS NULL AND platform=? AND source='llm_autonomous'
             AND reasoning LIKE '%FUTTIES%85%' AND dismissed_at IS NULL
             AND ts_utc >= datetime('now', '-6 hours')""",
        (platform,),
    ).fetchone()
    if existing:
        return None

    snap = con.execute(
        """SELECT cheapest_bin FROM fodder_snapshots
           WHERE rating=85 AND platform=? ORDER BY ts_utc DESC LIMIT 1""",
        (platform,),
    ).fetchone()
    price_str = f"{snap[0]:,}" if snap and snap[0] else "N/A"

    verdict: dict[str, Any] = {
        "verdict": "buy",
        "confidence": 85,
        "reasoning": (
            f"FUTTIES is active with a repeatable 85x10 SBC. Demand for 85-rated fodder is "
            f"structural and sustained for weeks, not hype-driven. Current cheapest BIN: {price_str}. "
            f"Buy at or near fodder price and list once SBC demand lifts the floor."
        ),
        "price_context": f"Current 85-rated cheapest BIN: {price_str}. Structural demand from repeatable SBC.",
        "risk": "low",
        "suggested_buy_price": snap[0] if snap else None,
        "suggested_sell_price": None,
        "horizon": "medium (days)",
    }
    rec = _insert_recommendation(con, None, platform, verdict, source="llm_autonomous")
    rec["player_name"] = "Fodder 85 (FUTTIES)"
    rec["version_name"] = "fodder"
    return rec


# ---------------------------------------------------------------------------
# Card recommendations
# ---------------------------------------------------------------------------

def generate_recommendations(platform: str, db_path: str, max_recs: int = 3) -> list[dict[str, Any]]:
    """
    Generate autonomous buy/avoid recommendations for the given platform.
    Returns list of inserted recommendation dicts.
    """
    if _anthropic is None:
        raise ImportError("anthropic package not installed")

    autonomous_spent = _check_autonomous_budget(db_path)
    if autonomous_spent > 0.02:
        logger.info("Daily autonomous budget exhausted ($%.4f >= $0.02) — skipping run.", autonomous_spent)
        return []

    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    config = _load_config()
    model = config.get("model", "claude-haiku-4-5-20251001")
    max_tokens = int(config.get("max_tokens", 500))
    cap_usd = float(config.get("daily_cap_usd", 0.50))

    _check_daily_cap(db_path, cap_usd)

    client = _anthropic.Anthropic(api_key=api_key)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    results: list[dict[str, Any]] = []

    try:
        candidates = _get_candidates(con, platform, limit=20)
        logger.info("Recommender: %d candidates for %s", len(candidates), platform)

        should_run, skip_reason = _has_worthy_candidates(candidates, platform, db_path)
        if not should_run:
            logger.info("Recommender: skipping — %s", skip_reason)
            return []

        for card in candidates:
            card_id = card["card_id"]

            if _has_recent_rec(con, card_id, platform, hours=10, player_name=card["player_name"]):
                logger.debug("Skipping %s — recent rec exists", card["player_name"])
                continue

            momentum = _get_price_momentum(con, card_id, platform)
            if not momentum["price_now"]:
                continue

            days = _days_since_last_rec(con, card_id, platform)
            card["_days_since_rec"] = days

            context = build_context(card["player_name"], platform, db_path)
            user_message = _format_card_message(card, context, momentum)

            try:
                verdict, in_tok, out_tok = _call_llm(
                    client, model, max_tokens, _AUTONOMOUS_SYSTEM_PROMPT, user_message
                )
            except Exception as exc:
                logger.warning("LLM call failed for %s: %s", card["player_name"], exc)
                continue

            _log_call(db_path, model, in_tok, out_tok, user_message, json.dumps(verdict), feature="autonomous")

            if float(verdict.get("confidence", 0)) < 60:
                logger.debug("Low confidence for %s (%s) — skip", card["player_name"], verdict.get("confidence"))
                continue

            if verdict.get("verdict") == "hold":
                logger.debug("Hold verdict for %s — skip", card["player_name"])
                continue

            rec = _insert_recommendation(con, card_id, platform, verdict)
            rec["player_name"] = card["player_name"]
            rec["version_name"] = card["version_name"]
            results.append(rec)

            if len(results) >= max_recs:
                break

        # Run fodder sweep after cards
        fodder_recs = _fodder_recommendations(con, platform, db_path, client, model, max_tokens, cap_usd)
        results.extend(fodder_recs)

        # FUTTIES structural fodder rec for rating 85
        cal = _calendar_context()
        if cal.get("futties_active"):
            futties_rec = _futties_85_recommendation(con, platform)
            if futties_rec:
                results.append(futties_rec)

    finally:
        con.close()

    logger.info("Recommender done: %d recs for %s", len(results), platform)
    return results


# ---------------------------------------------------------------------------
# Fodder recommendations
# ---------------------------------------------------------------------------

def _fodder_recommendations(
    con: sqlite3.Connection,
    platform: str,
    db_path: str,
    client: Any,
    model: str,
    max_tokens: int,
    cap_usd: float,
) -> list[dict[str, Any]]:
    """Scan ratings 82-91 for fodder buy opportunities."""
    calendar = _calendar_context()
    upcoming_promos = [p for p in calendar.get("promos", []) if (p.get("days_until_start") or 999) <= 14]
    if not upcoming_promos:
        return []

    results: list[dict[str, Any]] = []

    for rating in range(82, 92):
        try:
            _check_daily_cap(db_path, cap_usd)
        except RuntimeError:
            break

        snap = con.execute(
            """SELECT cheapest_bin, ts_utc FROM fodder_snapshots
               WHERE rating=? AND platform=? ORDER BY ts_utc DESC LIMIT 1""",
            (rating, platform),
        ).fetchone()
        if not snap or not snap[0]:
            continue

        cheapest_now = snap[0]

        week_low = con.execute(
            """SELECT MIN(cheapest_bin) FROM fodder_snapshots
               WHERE rating=? AND platform=? AND ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days') AND cheapest_bin > 0""",
            (rating, platform),
        ).fetchone()
        week_min = week_low[0] if week_low else None

        if not week_min or cheapest_now > week_min * 1.10:
            continue

        # Check for recent fodder rec (same rating)
        existing = con.execute(
            """SELECT id FROM recommendations
               WHERE card_id IS NULL AND platform=? AND source='llm_autonomous'
                 AND reasoning LIKE ? AND dismissed_at IS NULL
                 AND ts_utc >= datetime('now', '-6 hours')""",
            (platform, f"%rating {rating}%"),
        ).fetchone()
        if existing:
            continue

        week_high = con.execute(
            """SELECT MAX(cheapest_bin) FROM fodder_snapshots
               WHERE rating=? AND platform=? AND ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')""",
            (rating, platform),
        ).fetchone()
        week_max = week_high[0] if week_high else None

        promo_names = ", ".join(p["name"] for p in upcoming_promos)
        user_message = (
            f"Rating: {rating} fodder\n"
            f"Platform: {platform.upper()}\n"
            f"Current cheapest BIN: {cheapest_now:,}\n"
            f"7-day low: {week_min:,}\n"
            f"7-day high: {week_max or 'N/A'}\n"
            f"Upcoming promos in 14 days: {promo_names}\n\n"
            f"Is this fodder rating a buy opportunity right now?"
        )

        try:
            verdict, in_tok, out_tok = _call_llm(client, model, max_tokens, _FODDER_SYSTEM_PROMPT, user_message)
        except Exception as exc:
            logger.warning("Fodder LLM failed for rating %d: %s", rating, exc)
            continue

        _log_call(db_path, model, in_tok, out_tok, user_message, json.dumps(verdict), feature="autonomous")

        if float(verdict.get("confidence", 0)) < 60 or verdict.get("verdict") in ("hold", "avoid"):
            continue

        verdict["reasoning"] = f"[Fodder rating {rating}] " + verdict.get("reasoning", "")
        rec = _insert_recommendation(con, None, platform, verdict)
        rec["player_name"] = f"Fodder {rating}"
        rec["version_name"] = "fodder"
        results.append(rec)

    return results


# ---------------------------------------------------------------------------
# Outcome evaluator
# ---------------------------------------------------------------------------

def evaluate_outcomes(db_path: str) -> int:
    """
    Evaluate recommendations older than 24h that have no outcome yet.
    Returns count of outcomes written.
    """
    con = sqlite3.connect(db_path)
    count = 0

    try:
        recs = con.execute(
            """SELECT r.id, r.card_id, r.platform, r.call, r.ts_utc
               FROM recommendations r
               WHERE r.ts_utc <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-24 hours')
                 AND r.dismissed_at IS NULL
                 AND NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.recommendation_id = r.id)""",
        ).fetchall()

        for rec_id, card_id, platform, call, ts_utc in recs:
            if card_id is None:
                # Fodder rec: mark as expired (no per-card price to evaluate)
                con.execute(
                    """INSERT INTO outcomes (recommendation_id, evaluated_at_utc, verdict, notes)
                       VALUES (?, datetime('now'), 'expired', 'fodder rec — no card_id')""",
                    (rec_id,),
                )
                count += 1
                continue

            price_at_call = con.execute(
                """SELECT bin_price FROM price_snapshots
                   WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
                     AND ts_utc <= ?
                   ORDER BY ts_utc DESC LIMIT 1""",
                (card_id, platform, ts_utc),
            ).fetchone()

            price_now_row = con.execute(
                """SELECT bin_price FROM price_snapshots
                   WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
                   ORDER BY ts_utc DESC LIMIT 1""",
                (card_id, platform),
            ).fetchone()

            if not price_at_call or not price_now_row:
                verdict = "expired"
                p_call = None
                p_now = None
            else:
                p_call = price_at_call[0]
                p_now = price_now_row[0]
                if p_call > 0:
                    change_pct = (p_now - p_call) / p_call * 100
                else:
                    change_pct = 0

                if abs(change_pct) < 5:
                    verdict = "neutral"
                elif call == "buy" and change_pct > 5:
                    verdict = "correct"
                elif call == "avoid" and change_pct < -5:
                    verdict = "correct"
                else:
                    verdict = "incorrect"

            con.execute(
                """INSERT INTO outcomes (recommendation_id, evaluated_at_utc, price_at_call, price_now, verdict)
                   VALUES (?, datetime('now'), ?, ?, ?)""",
                (rec_id, p_call, p_now, verdict),
            )
            count += 1

        con.commit()
        logger.info("Outcome evaluator: %d outcomes written", count)
    except Exception as exc:
        logger.error("Outcome evaluator failed: %s", exc)
    finally:
        con.close()

    return count
