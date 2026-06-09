"""
FCPriceMaster LLM Ask feature — Claude Haiku trade-call analysis.

CLI usage (from backend/):
  echo '{"text": "Wirtz gold under 63K on console", "platform": "console"}' | uv run python -m src.llm.ask

Returns structured JSON verdict to stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None  # type: ignore[assignment]

from src.llm.context_builder import build_context

logger = logging.getLogger(__name__)

_DB_PATH = str(Path(__file__).parents[3] / "data" / "fcpricemaster.db")
_CONFIG_PATH = Path(__file__).parents[3] / "config" / "llm_config.yaml"
_ENV_PATH = Path(__file__).parents[3] / ".env"

# Haiku 4.5 token costs (USD per token)
_INPUT_COST_PER_TOKEN = 0.00000025   # $0.25 / 1M
_OUTPUT_COST_PER_TOKEN = 0.00000125  # $1.25 / 1M

_SYSTEM_PROMPT = """You are FCPriceMaster, an EA FC Ultimate Team market analyst. You have deep knowledge of how the FUT transfer market works, including:

Promo cycles (TOTW, TOTY, FUT Birthday, TOTS, Winter Wildcards, etc.)
How promo releases cause price spikes followed by corrections
How SBCs drive demand for fodder cards at specific ratings
How TOTW makes a player's gold card go OOP (out of packs), often increasing its price if used in SBCs
How market hype causes temporary over-pricing that typically corrects within 24-48h
The difference between PC and Console markets

When evaluating a trade call, always consider:

Current price vs historical baseline (is this already hyped/overpriced?)
Upcoming calendar events that could affect demand
Whether the reasoning in the call is sound given current market data
Hold time vs risk

End-of-cycle awareness (apply based on days_to_next_launch in the user message):
- days_to_next_launch > 120: do NOT mention the next game. It is irrelevant.
- 60-120 days: for LONG horizon trades only, briefly note that end-of-cycle selling pressure may build.
- 30-60 days: factor it for medium and long trades. Market will progressively weaken.
- < 30 days: factor it for ALL trades. Market is dying; almost nothing is worth buying except specific end-of-cycle plays.
Always refer to "next game launch" generically — never hardcode a game name.

FUTTIES rules (apply only when futties_active=True in the user message):
- 85-rated gold cards: STRONG BUY signal due to repeatable 85x10 SBC demand. Note this explicitly.
- All other cards: heavy AVOID bias. Repeatable packs flood supply; price recovery before game end is unlikely.
If futties_days_until < 30: warn about approaching FUTTIES for any long-horizon hold recommendations.

Signals tagged irl_transfer or irl_result are real-world football news, not FUT market data. A real-world transfer fee (e.g. €150M to Real Madrid) does NOT indicate a FUT card price. A high-profile IRL move may create mild in-game demand — treat as weak positive sentiment only, never as price evidence. Signals tagged promo_leak are high priority.

Always respond in this exact JSON format:
{
  "verdict": "buy" | "hold" | "avoid",
  "confidence": 0-100,
  "reasoning": "2-3 sentence explanation",
  "price_context": "what current prices tell us",
  "risk": "low" | "medium" | "high",
  "suggested_buy_price": null or number,
  "suggested_sell_price": null or number,
  "horizon": "short (hours)" | "medium (days)" | "long (weeks)"
}
Respond ONLY with the JSON object. No preamble, no markdown fences."""


def _load_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}


def _load_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    # Try reading from .env file directly (supports both KEY=VALUE and label-value formats)
    try:
        content = _ENV_PATH.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY"):
                if "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _check_daily_cap(db_path: str, cap_usd: float) -> tuple[float, int]:
    """Return (today_total_usd, call_count). Raises RuntimeError if cap exceeded."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM llm_calls WHERE ts_utc >= ?",
            (today + "T00:00:00Z",),
        ).fetchone()
        con.close()
        total_cost, count = row[0], row[1]
        if total_cost >= cap_usd:
            raise RuntimeError(
                f"Daily AI budget reached (${cap_usd:.2f}). Resets at midnight UTC."
            )
        return total_cost, count
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("Could not check daily cap: %s", exc)
        return 0.0, 0


def _log_call(
    db_path: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    input_text: str,
    output_json: str,
    feature: str = "ask",
) -> None:
    cost = input_tokens * _INPUT_COST_PER_TOKEN + output_tokens * _OUTPUT_COST_PER_TOKEN
    try:
        con = sqlite3.connect(db_path)
        con.execute(
            """INSERT INTO llm_calls
               (model, input_tokens, output_tokens, cost_usd, feature, input_text, output_json)
               VALUES (?,?,?,?,?,?,?)""",
            (model, input_tokens, output_tokens, cost, feature, input_text, output_json),
        )
        con.commit()
        con.close()
    except Exception as exc:
        logger.warning("Failed to log LLM call: %s", exc)


def _format_user_message(context: dict[str, Any], trade_call: str) -> str:
    """Build the structured user message from context + raw trade call text."""
    lines = [f"Trade call: {trade_call}", ""]

    cal = context.get("release_calendar", {})
    lines.append(f"Today: {cal.get('today', 'unknown')}")

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

    for promo in cal.get("promos", []):
        if promo.get("in_window"):
            lines.append(f"ACTIVE PROMO: {promo['name']} (ends {promo.get('window_end', '?')})")
        elif promo.get("days_until_start") is not None:
            lines.append(f"Upcoming: {promo['name']} in {promo['days_until_start']} days")
    lines.append("")

    platform = context.get("platform", "console")
    lines.append(f"Platform: {platform.upper()}")
    lines.append("")

    cards = context.get("mentioned_cards", [])
    if cards:
        lines.append("Mentioned cards:")
        for c in cards:
            price = c.get("current_price")
            price_str = f"{price:,}" if price else "N/A"
            change = c.get("change_24h_pct")
            change_str = f"{change:+.1f}%" if change is not None else "N/A"
            lines.append(
                f"  - {c['player_name']} ({c['version_name']}): "
                f"{price_str} coins | 24h: {change_str} | trend: {c.get('trend', 'unknown')} | "
                f"7d range: {c.get('week_low') or 'N/A'}-{c.get('week_high') or 'N/A'}"
            )
        lines.append("")

    fodder = context.get("fodder_context", [])
    if fodder:
        lines.append("Fodder context:")
        for f in fodder:
            lines.append(
                f"  - Rating {f['rating']}: cheapest {f.get('cheapest_bin') or 'N/A'}, "
                f"median {f.get('median_bin') or 'N/A'}"
            )
        lines.append("")

    signals = context.get("recent_signals", [])
    if signals:
        lines.append("Recent signals:")
        for s in signals[:5]:
            text = (s.get("text") or "")[:120]
            context = s.get("signal_context", "fut_market")
            lines.append(f"  [{s.get('source', '?')} | {context}] {text}")
        lines.append("")

    return "\n".join(lines)


def ask(
    text: str,
    platform: str = "console",
    db_path: str | None = None,
) -> dict[str, Any]:
    """
    Build context, call Claude Haiku, return structured verdict dict.
    Also logs the call to llm_calls and enforces the daily cap.
    """
    if _anthropic is None:
        raise ImportError("anthropic package not installed — run: uv add anthropic")

    db = db_path or _DB_PATH
    config = _load_config()
    model = config.get("model", "claude-haiku-4-5-20251001")
    max_tokens = int(config.get("max_tokens", 1000))
    temperature = float(config.get("temperature", 0))
    cap_usd = float(config.get("daily_cap_usd", 0.50))

    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found. Add it to .env or set the environment variable."
        )

    # Check daily cap before making the call
    _check_daily_cap(db, cap_usd)

    # Build context
    context = build_context(text, platform, db)
    user_message = _format_user_message(context, text)

    # Call Claude
    client = _anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = response.content[0].text.strip()
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = input_tokens * _INPUT_COST_PER_TOKEN + output_tokens * _OUTPUT_COST_PER_TOKEN

    # Strip markdown code fences if present
    clean_text = raw_text
    if clean_text.startswith("```"):
        lines = clean_text.split("\n")
        # Remove first and last fence lines
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        clean_text = "\n".join(lines[start:end]).strip()

    # Parse JSON response
    try:
        verdict = json.loads(clean_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON: {raw_text[:200]}") from exc

    # Validate required fields
    required = ["verdict", "confidence", "reasoning", "price_context", "risk", "horizon"]
    missing = [f for f in required if f not in verdict]
    if missing:
        raise ValueError(f"LLM response missing fields: {missing}")

    # Log the call
    _log_call(
        db, model, input_tokens, output_tokens,
        input_text=text,
        output_json=json.dumps(verdict),
    )

    return {
        "verdict": verdict,
        "context_used": {
            "cards": [c["player_name"] for c in context.get("mentioned_cards", [])],
            "fodder_ratings": [f["rating"] for f in context.get("fodder_context", [])],
            "signals_count": len(context.get("recent_signals", [])),
        },
        "usage": {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
        },
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except ImportError:
        pass

    # Read input: stdin JSON or first arg
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    elif len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        raw = '{"text": "test call", "platform": "console"}'

    try:
        params = json.loads(raw)
    except json.JSONDecodeError:
        params = {"text": raw, "platform": "console"}

    trade_text = params.get("text", "")
    plat = params.get("platform", "console")

    try:
        result = ask(trade_text, platform=plat)
        print(json.dumps(result, indent=2))
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
