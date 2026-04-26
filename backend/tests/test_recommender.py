"""Tests for the autonomous recommender and outcome evaluator."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.db.migrate import run_migrations
from src.llm.recommender import (
    _get_candidates,
    _has_recent_rec,
    _parse_horizon,
    _get_price_momentum,
    _insert_recommendation,
    evaluate_outcomes,
)


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
def db_with_cards(tmp_db):
    con = sqlite3.connect(tmp_db)
    # Insert 3 cards
    for i, name in enumerate(["Vinicius Jr", "Mbappe", "Haaland"], start=1):
        con.execute(
            "INSERT INTO cards (card_key, player_name, version_name, game_edition) VALUES (?,?,?,?)",
            (f"card-{i}", name, "TOTS", "fc26"),
        )
    con.commit()
    con.close()
    return tmp_db


def _add_snapshots(db_path: str, card_id: int, platform: str, count: int, price: int = 100000) -> None:
    con = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc)
    for i in range(count):
        ts = (now - timedelta(hours=i * 6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
            (card_id, platform, ts, price, "futgg", "fc26"),
        )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# _parse_horizon
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("horizon,expected", [
    ("short (hours)", 12),
    ("medium (days)", 72),
    ("long (weeks)", 168),
    ("MEDIUM (Days)", 72),
    ("unknown", 48),
])
def test_parse_horizon(horizon, expected):
    assert _parse_horizon(horizon) == expected


# ---------------------------------------------------------------------------
# _get_candidates
# ---------------------------------------------------------------------------

def test_get_candidates_empty(db_with_cards):
    con = sqlite3.connect(db_with_cards)
    result = _get_candidates(con, "pc", limit=10)
    con.close()
    assert result == []


def test_get_candidates_pool_b_with_two_snapshots(db_with_cards):
    """Pool B (7d low) picks up a card with 2 snapshots at a stable price."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    _add_snapshots(db_with_cards, card_id, "pc", 2)  # 2 snapshots at flat price
    con = sqlite3.connect(db_with_cards)
    result = _get_candidates(con, "pc")
    con.close()
    # Pool B picks it up since current == week_low (within 10%)
    assert len(result) == 1
    assert result[0]["card_id"] == card_id
    assert result[0]["_pool"] == "7d_low"


def test_get_candidates_pool_c_requires_three_snapshots(db_with_cards):
    """Pool C (trending fallback) requires 3+ snapshots in 48h; Pool B picks up flat-price cards anyway."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    _add_snapshots(db_with_cards, card_id, "pc", 4)  # 4 snapshots — qualifies in Pool B or C
    con = sqlite3.connect(db_with_cards)
    result = _get_candidates(con, "pc")
    con.close()
    assert len(result) == 1
    assert result[0]["card_id"] == card_id


def test_get_candidates_only_platform(db_with_cards):
    cards = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards").fetchall()
    pc_id, console_id = cards[0][0], cards[1][0]
    _add_snapshots(db_with_cards, pc_id, "pc", 4)
    _add_snapshots(db_with_cards, console_id, "console", 4)
    con = sqlite3.connect(db_with_cards)
    pc_result = _get_candidates(con, "pc")
    console_result = _get_candidates(con, "console")
    con.close()
    assert all(r["card_id"] == pc_id for r in pc_result)
    assert all(r["card_id"] == console_id for r in console_result)


# ---------------------------------------------------------------------------
# _has_recent_rec
# ---------------------------------------------------------------------------

def test_has_recent_rec_false_when_empty(db_with_cards):
    con = sqlite3.connect(db_with_cards)
    assert _has_recent_rec(con, 1, "pc") is False
    con.close()


def test_has_recent_rec_true_when_recent(db_with_cards):
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)
    con.execute(
        "INSERT INTO recommendations (card_id, platform, call, confidence, source) VALUES (?,?,?,?,?)",
        (card_id, "pc", "buy", 80.0, "llm_autonomous"),
    )
    con.commit()
    assert _has_recent_rec(con, card_id, "pc", hours=6) is True
    con.close()


def test_has_recent_rec_dismissed_does_not_count(db_with_cards):
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)
    con.execute(
        """INSERT INTO recommendations (card_id, platform, call, confidence, source, dismissed_at)
           VALUES (?,?,?,?,?,datetime('now'))""",
        (card_id, "pc", "buy", 80.0, "llm_autonomous"),
    )
    con.commit()
    assert _has_recent_rec(con, card_id, "pc", hours=6) is False
    con.close()


# ---------------------------------------------------------------------------
# evaluate_outcomes
# ---------------------------------------------------------------------------

def test_evaluate_outcomes_no_recs(db_with_cards):
    count = evaluate_outcomes(db_with_cards)
    assert count == 0


def test_evaluate_outcomes_buy_correct_on_price_rise(db_with_cards):
    """A buy call where price rose >5% should be marked correct."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)

    # Insert old price snapshot (at time of call)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", old_ts, 100000, "futgg", "fc26"),
    )
    # Insert recent price snapshot (price rose 15%)
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", now_ts, 115000, "futgg", "fc26"),
    )
    # Insert recommendation older than 24h
    rec_ts = (datetime.now(timezone.utc) - timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO recommendations (card_id, platform, ts_utc, call, confidence, source) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", rec_ts, "buy", 75.0, "llm_autonomous"),
    )
    con.commit()
    con.close()

    count = evaluate_outcomes(db_with_cards)
    assert count == 1

    con = sqlite3.connect(db_with_cards)
    row = con.execute("SELECT verdict FROM outcomes LIMIT 1").fetchone()
    con.close()
    assert row[0] == "correct"


def test_evaluate_outcomes_avoid_incorrect_on_price_rise(db_with_cards):
    """An avoid call where price rose >5% should be marked incorrect."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", old_ts, 100000, "futgg", "fc26"),
    )
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", now_ts, 120000, "futgg", "fc26"),
    )
    rec_ts = (datetime.now(timezone.utc) - timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO recommendations (card_id, platform, ts_utc, call, confidence, source) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", rec_ts, "avoid", 75.0, "llm_autonomous"),
    )
    con.commit()
    con.close()

    evaluate_outcomes(db_with_cards)

    con = sqlite3.connect(db_with_cards)
    row = con.execute("SELECT verdict FROM outcomes LIMIT 1").fetchone()
    con.close()
    assert row[0] == "incorrect"


def test_evaluate_outcomes_neutral_on_small_move(db_with_cards):
    """A <5% price move should be marked neutral."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", old_ts, 100000, "futgg", "fc26"),
    )
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", now_ts, 102000, "futgg", "fc26"),
    )
    rec_ts = (datetime.now(timezone.utc) - timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        "INSERT INTO recommendations (card_id, platform, ts_utc, call, confidence, source) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", rec_ts, "buy", 70.0, "llm_autonomous"),
    )
    con.commit()
    con.close()

    evaluate_outcomes(db_with_cards)

    con = sqlite3.connect(db_with_cards)
    row = con.execute("SELECT verdict FROM outcomes LIMIT 1").fetchone()
    con.close()
    assert row[0] == "neutral"


def test_evaluate_outcomes_too_new_not_evaluated(db_with_cards):
    """Recs younger than 24h should not be evaluated."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)
    # Rec is only 2h old
    con.execute(
        "INSERT INTO recommendations (card_id, platform, call, confidence, source) VALUES (?,?,?,?,?)",
        (card_id, "pc", "buy", 80.0, "llm_autonomous"),
    )
    con.commit()
    con.close()

    count = evaluate_outcomes(db_with_cards)
    assert count == 0


def test_evaluate_outcomes_not_reevaluated(db_with_cards):
    """A rec that already has an outcome should not be evaluated again."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)
    rec_ts = (datetime.now(timezone.utc) - timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = con.execute(
        "INSERT INTO recommendations (card_id, platform, ts_utc, call, confidence, source) VALUES (?,?,?,?,?,?)",
        (card_id, "pc", rec_ts, "buy", 80.0, "llm_autonomous"),
    )
    rec_id = cur.lastrowid
    con.execute(
        "INSERT INTO outcomes (recommendation_id, evaluated_at_utc, verdict) VALUES (?,datetime('now'),'correct')",
        (rec_id,),
    )
    con.commit()
    con.close()

    count = evaluate_outcomes(db_with_cards)
    assert count == 0


# ---------------------------------------------------------------------------
# generate_recommendations (mocked LLM)
# ---------------------------------------------------------------------------

def test_generate_recommendations_filters_hold_and_low_confidence(db_with_cards):
    """generate_recommendations should filter hold verdicts and confidence < 60."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    _add_snapshots(db_with_cards, card_id, "pc", 4)

    buy_response = MagicMock()
    buy_response.content = [MagicMock(text=json.dumps({
        "verdict": "buy", "confidence": 75, "reasoning": "test", "price_context": "low",
        "risk": "medium", "suggested_buy_price": 90000, "suggested_sell_price": None,
        "horizon": "medium (days)",
    }))]
    buy_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    hold_response = MagicMock()
    hold_response.content = [MagicMock(text=json.dumps({
        "verdict": "hold", "confidence": 80, "reasoning": "meh", "price_context": "stable",
        "risk": "low", "suggested_buy_price": None, "suggested_sell_price": None,
        "horizon": "long (weeks)",
    }))]
    hold_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    low_conf_response = MagicMock()
    low_conf_response.content = [MagicMock(text=json.dumps({
        "verdict": "avoid", "confidence": 40, "reasoning": "maybe", "price_context": "high",
        "risk": "high", "suggested_buy_price": None, "suggested_sell_price": None,
        "horizon": "short (hours)",
    }))]
    low_conf_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    with patch("src.llm.recommender._load_api_key", return_value="test-key"), \
         patch("src.llm.recommender._check_daily_cap", return_value=(0.0, 0)), \
         patch("src.llm.recommender._log_call"), \
         patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        # First call: buy (should be included), second: hold (filtered), third: low conf (filtered)
        mock_client.messages.create.side_effect = [buy_response, hold_response, low_conf_response]

        from src.llm.recommender import generate_recommendations
        results = generate_recommendations("pc", db_with_cards, max_recs=10)

    # Only the buy with confidence 75 should survive
    assert len(results) == 1
    assert results[0]["call"] == "buy"
    assert results[0]["confidence"] == 75.0
