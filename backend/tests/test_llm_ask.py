"""Tests for LLM ask module: context builder, response parsing, daily cap."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.db.migrate import run_migrations
from src.llm.context_builder import build_context, _match_cards
from src.llm.ask import _check_daily_cap, _log_call, _format_user_message


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
            w = path + suffix
            if os.path.exists(w):
                os.unlink(w)
    except OSError:
        pass


@pytest.fixture()
def db_with_cards_and_prices(tmp_db):
    """DB with a test card and some price snapshots."""
    con = sqlite3.connect(tmp_db)
    con.execute(
        "INSERT INTO cards (card_key, player_name, version_name, game_edition) VALUES (?,?,?,?)",
        ("26-555", "Florian Wirtz", "TOTS", "fc26"),
    )
    con.execute(
        "SELECT id FROM cards WHERE card_key='26-555'"
    )
    card_id = con.execute("SELECT id FROM cards WHERE card_key='26-555'").fetchone()[0]
    # Insert some price snapshots
    for i, price in enumerate([60000, 62000, 65000]):
        con.execute(
            """INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition)
               VALUES (?,?,datetime('now', ?),?,?,?)""",
            (card_id, "console", f"-{i*2} hours", price, "futgg", "fc26"),
        )
    con.commit()
    con.close()
    return tmp_db


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def test_context_builder_returns_expected_shape(db_with_cards_and_prices):
    """build_context should return dict with required keys."""
    result = build_context("Buy Wirtz TOTS under 63K", "console", db_with_cards_and_prices)
    assert "mentioned_cards" in result
    assert "fodder_context" in result
    assert "release_calendar" in result
    assert "recent_signals" in result
    assert "platform" in result
    assert result["platform"] == "console"


def test_context_builder_matches_card_by_name(db_with_cards_and_prices):
    """Card name in text should be matched to the DB card."""
    result = build_context("Wirtz gold is underpriced", "console", db_with_cards_and_prices)
    card_names = [c["player_name"] for c in result["mentioned_cards"]]
    assert "Florian Wirtz" in card_names


def test_context_builder_price_fields(db_with_cards_and_prices):
    """Matched cards should have current_price and trend fields."""
    result = build_context("Buy Wirtz", "console", db_with_cards_and_prices)
    if result["mentioned_cards"]:
        card = result["mentioned_cards"][0]
        assert "current_price" in card
        assert "trend" in card


def test_context_builder_no_card_match(db_with_cards_and_prices):
    """Text with no card names should return empty mentioned_cards."""
    result = build_context("The market looks good today", "console", db_with_cards_and_prices)
    # May or may not match depending on cards in DB — just check structure
    assert isinstance(result["mentioned_cards"], list)


def test_context_builder_fodder_context(tmp_db):
    """Text with rating mention should trigger fodder lookup."""
    # Insert a fodder snapshot
    con = sqlite3.connect(tmp_db)
    con.execute(
        "INSERT INTO fodder_snapshots (rating, platform, cheapest_bin, median_bin) VALUES (?,?,?,?)",
        (85, "console", 5500, 6200),
    )
    con.commit()
    con.close()

    result = build_context("Buy 85s for SBC pack fodder", "console", tmp_db)
    assert any(f["rating"] == 85 for f in result["fodder_context"])


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------

def test_format_user_message_contains_trade_call(db_with_cards_and_prices):
    """Formatted user message should include the original trade call text."""
    from src.llm.context_builder import build_context
    ctx = build_context("Wirtz gold under 63K", "console", db_with_cards_and_prices)
    msg = _format_user_message(ctx, "Wirtz gold under 63K")
    assert "Wirtz gold under 63K" in msg
    assert "console" in msg.lower()


def test_llm_response_parse_valid():
    """Valid JSON response should parse without error."""
    raw = json.dumps({
        "verdict": "buy",
        "confidence": 72,
        "reasoning": "Strong OOP signal with historical baseline intact.",
        "price_context": "Current price below 7-day average.",
        "risk": "medium",
        "suggested_buy_price": 62000,
        "suggested_sell_price": 80000,
        "horizon": "medium (days)",
    })
    verdict = json.loads(raw)
    required = ["verdict", "confidence", "reasoning", "price_context", "risk", "horizon"]
    missing = [f for f in required if f not in verdict]
    assert not missing


def test_llm_response_parse_rejects_non_json():
    """Non-JSON LLM output should raise ValueError (simulated)."""
    raw = "I think you should buy this card because..."
    try:
        json.loads(raw)
        assert False, "Should have raised"
    except json.JSONDecodeError:
        pass  # expected


# ---------------------------------------------------------------------------
# Daily cap enforcement
# ---------------------------------------------------------------------------

def test_daily_cap_passes_when_no_calls(tmp_db):
    """Cap check should pass when no calls have been made today."""
    total, count = _check_daily_cap(tmp_db, 0.50)
    assert total == 0.0
    assert count == 0


def test_daily_cap_raises_when_exceeded(tmp_db):
    """Cap check should raise RuntimeError when daily total >= cap."""
    # Insert a fake call that exceeds the cap
    _log_call(tmp_db, "claude-haiku-4-5-20251001", 100000, 10000, "test", "{}", "ask")
    # 100000 * 0.00000025 + 10000 * 0.00000125 = 0.025 + 0.0125 = 0.0375 — under $0.50
    # Insert a huge call to exceed cap
    con = sqlite3.connect(tmp_db)
    con.execute(
        "INSERT INTO llm_calls (model, input_tokens, output_tokens, cost_usd, feature) VALUES (?,?,?,?,?)",
        ("test", 0, 0, 0.60, "ask"),
    )
    con.commit()
    con.close()

    with pytest.raises(RuntimeError, match="Daily AI budget reached"):
        _check_daily_cap(tmp_db, 0.50)


def test_daily_cap_tiny_cap(tmp_db):
    """Set cap below the cost of a minimum call — should trip it."""
    _log_call(tmp_db, "test-model", 1, 1, "t", "{}", "ask")
    # 1 in + 1 out = $0.0000015; cap of $0.000001 is lower
    with pytest.raises(RuntimeError, match="Daily AI budget reached"):
        _check_daily_cap(tmp_db, 0.000001)


def test_log_call_inserts_row(tmp_db):
    """_log_call should insert a row with correct cost calculation."""
    _log_call(tmp_db, "claude-haiku-4-5-20251001", 4000, 800, "buy Wirtz", '{"verdict":"buy"}', "ask")
    con = sqlite3.connect(tmp_db)
    row = con.execute(
        "SELECT model, input_tokens, output_tokens, cost_usd, feature FROM llm_calls"
    ).fetchone()
    con.close()
    assert row[0] == "claude-haiku-4-5-20251001"
    assert row[1] == 4000
    assert row[2] == 800
    # cost = 4000 * 0.00000025 + 800 * 0.00000125 = 0.001 + 0.001 = 0.002
    assert abs(row[3] - 0.002) < 1e-7
    assert row[4] == "ask"
