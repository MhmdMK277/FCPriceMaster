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
    """Return top candidate cards: 3+ snapshots in last 48h, sorted by signal count + volatility."""
    rows = con.execute(
        """
        WITH eligible AS (
            SELECT card_id, COUNT(*) AS snap_count
            FROM price_snapshots
            WHERE platform=? AND ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-48 hours') AND bin_price IS NOT NULL
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
        ORDER BY COALESCE(sc.sig_count, 0) DESC
        LIMIT ?
        """,
        (platform, limit),
    ).fetchall()
    return [
        {
            "card_id": r[0], "player_name": r[1], "version_name": r[2],
            "card_key": r[3], "signal_count_24h": r[4],
        }
        for r in rows
    ]


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


def _has_recent_rec(con: sqlite3.Connection, card_id: int | None, platform: str, hours: int = 6) -> bool:
    if card_id is None:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
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
# Card recommendations
# ---------------------------------------------------------------------------

def generate_recommendations(platform: str, db_path: str, max_recs: int = 10) -> list[dict[str, Any]]:
    """
    Generate autonomous buy/avoid recommendations for the given platform.
    Returns list of inserted recommendation dicts.
    """
    if _anthropic is None:
        raise ImportError("anthropic package not installed")

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

        for card in candidates:
            card_id = card["card_id"]

            if _has_recent_rec(con, card_id, platform, hours=6):
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
