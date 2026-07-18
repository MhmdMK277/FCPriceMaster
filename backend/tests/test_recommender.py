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
    _check_autonomous_budget,
    _has_worthy_candidates,
    _get_budget_status,
    evaluate_outcomes,
    generate_recommendations,
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


def test_get_candidates_two_snapshots_blocked_by_staleness_guard(db_with_cards):
    """Session 35: cards with fewer than MIN_SNAPSHOTS (3) valid snapshots never
    become candidates, even if Pool B (7d low) would otherwise pick them up."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    _add_snapshots(db_with_cards, card_id, "pc", 2)  # 2 snapshots — below MIN_SNAPSHOTS
    con = sqlite3.connect(db_with_cards)
    result = _get_candidates(con, "pc")
    con.close()
    assert result == []


def test_get_candidates_pool_b_with_three_snapshots(db_with_cards):
    """Pool B (7d low) picks up a card with 3 fresh snapshots at a stable price."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    _add_snapshots(db_with_cards, card_id, "pc", 3)  # 3 snapshots at flat price
    con = sqlite3.connect(db_with_cards)
    result = _get_candidates(con, "pc")
    con.close()
    # Pool B picks it up since current == week_low (within 10%)
    assert len(result) == 1
    assert result[0]["card_id"] == card_id
    assert result[0]["_pool"] == "7d_low"


def test_staleness_guard_blocks_old_data(db_with_cards):
    """Session 35: cards whose newest snapshot is older than STALE_THRESHOLD_HOURS
    are filtered out of candidate selection entirely."""
    card_id = sqlite3.connect(db_with_cards).execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
    con = sqlite3.connect(db_with_cards)
    now = datetime.now(timezone.utc)
    for i in range(4):  # 4 snapshots, all 2+ days old
        ts = (now - timedelta(hours=50 + i * 6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
            (card_id, "pc", ts, 100000, "futgg", "fc26"),
        )
    con.commit()
    result = _get_candidates(con, "pc")
    con.close()
    assert result == []


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

def test_check_autonomous_budget_returns_zero_when_empty(db_with_cards):
    """No autonomous calls today → budget returns 0.0."""
    spent = _check_autonomous_budget(db_with_cards)
    assert spent == 0.0


def test_check_autonomous_budget_counts_only_autonomous(db_with_cards):
    """Only feature='autonomous' rows count toward the autonomous budget."""
    con = sqlite3.connect(db_with_cards)
    con.execute(
        "INSERT INTO llm_calls (model, input_tokens, output_tokens, cost_usd, feature) VALUES (?,?,?,?,?)",
        ("claude-haiku-4-5-20251001", 100, 50, 0.015, "autonomous"),
    )
    con.execute(
        "INSERT INTO llm_calls (model, input_tokens, output_tokens, cost_usd, feature) VALUES (?,?,?,?,?)",
        ("claude-haiku-4-5-20251001", 100, 50, 0.010, "ask"),
    )
    con.commit()
    con.close()
    spent = _check_autonomous_budget(db_with_cards)
    assert abs(spent - 0.015) < 1e-9


def test_generate_recommendations_skips_on_exhausted_autonomous_budget(db_with_cards):
    """If autonomous spend today > $0.02, generate_recommendations returns [] and skips LLM."""
    con = sqlite3.connect(db_with_cards)
    con.execute(
        "INSERT INTO llm_calls (model, input_tokens, output_tokens, cost_usd, feature) VALUES (?,?,?,?,?)",
        ("claude-haiku-4-5-20251001", 1000, 200, 0.025, "autonomous"),
    )
    con.commit()
    con.close()

    with patch("src.llm.recommender._load_api_key", return_value="test-key"), \
         patch("anthropic.Anthropic") as mock_cls:
        results = generate_recommendations("pc", db_with_cards, max_recs=3)
        mock_cls.assert_not_called()

    assert results == []


def test_generate_recommendations_filters_hold_and_low_confidence(db_with_cards):
    """generate_recommendations should filter hold verdicts and confidence < 60."""
    # Need 3+ candidates so _has_worthy_candidates passes (no-signal + <3 skips)
    all_ids = [r[0] for r in sqlite3.connect(db_with_cards).execute("SELECT id FROM cards").fetchall()]
    for cid in all_ids:
        _add_snapshots(db_with_cards, cid, "pc", 4)
    card_id = all_ids[0]

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

    # _calendar_context patched: the real release_calendar.yaml turns FUTTIES
    # active from 07-18, which appends the structural 85-fodder rec and breaks
    # the exact count this test asserts. Calendar behavior is tested elsewhere.
    with patch("src.llm.recommender._load_api_key", return_value="test-key"), \
         patch("src.llm.recommender._check_daily_cap", return_value=(0.0, 0)), \
         patch("src.llm.recommender._log_call"), \
         patch("src.llm.recommender._calendar_context", return_value={
             "today": "2026-01-01", "days_to_next_launch": None,
             "end_of_cycle_phase": "none", "futties_active": False,
             "futties_days_until": None, "promos": [],
         }), \
         patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        # First call: buy (should be included), second: hold (filtered), third: low conf (filtered)
        mock_client.messages.create.side_effect = [buy_response, hold_response, low_conf_response]

        results = generate_recommendations("pc", db_with_cards, max_recs=10)

    # Only the buy with confidence 75 should survive
    assert len(results) == 1
    assert results[0]["call"] == "buy"
    assert results[0]["confidence"] == 75.0


# ---------------------------------------------------------------------------
# tradeable column — untradeable cards excluded from candidates
# ---------------------------------------------------------------------------

def test_untradeable_card_excluded_from_candidates(tmp_db):
    """A card with tradeable=0 must never appear in _get_candidates output."""
    con = sqlite3.connect(tmp_db)
    # Insert untradeable card
    con.execute(
        "INSERT INTO cards (card_key, player_name, version_name, game_edition, tradeable) VALUES (?,?,?,?,?)",
        ("untr-1", "UntradeablePlayer", "Reward", "fc26", 0),
    )
    con.commit()
    card_id = con.execute("SELECT id FROM cards WHERE card_key='untr-1'").fetchone()[0]

    # Add enough price snapshots to qualify for Pool B and Pool C
    now = datetime.now(timezone.utc)
    for i in range(5):
        ts = (now - timedelta(hours=i * 6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
            (card_id, "pc", ts, 50000, "futgg", "fc26"),
        )
    con.commit()

    result = _get_candidates(con, "pc")
    con.close()
    assert all(r["card_id"] != card_id for r in result), "Untradeable card must not appear in candidates"


def test_tradeable_card_appears_in_candidates(tmp_db):
    """A card with tradeable=1 should appear in candidates when it has price snapshots."""
    con = sqlite3.connect(tmp_db)
    con.execute(
        "INSERT INTO cards (card_key, player_name, version_name, game_edition, tradeable) VALUES (?,?,?,?,?)",
        ("tr-1", "TradeablePlayer", "TOTS", "fc26", 1),
    )
    con.commit()
    card_id = con.execute("SELECT id FROM cards WHERE card_key='tr-1'").fetchone()[0]

    now = datetime.now(timezone.utc)
    for i in range(4):
        ts = (now - timedelta(hours=i * 6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO price_snapshots (card_id, platform, ts_utc, bin_price, source, game_edition) VALUES (?,?,?,?,?,?)",
            (card_id, "pc", ts, 80000, "futgg", "fc26"),
        )
    con.commit()

    result = _get_candidates(con, "pc")
    con.close()
    assert any(r["card_id"] == card_id for r in result), "Tradeable card should appear in candidates"


# ---------------------------------------------------------------------------
# _has_worthy_candidates
# ---------------------------------------------------------------------------

def test_has_worthy_candidates_empty_returns_false(tmp_db):
    """Empty candidate list must return (False, reason)."""
    should_run, reason = _has_worthy_candidates([], "pc", tmp_db)
    assert should_run is False
    assert "No candidate" in reason


def test_has_worthy_candidates_all_recent_recs_returns_false(db_with_cards):
    """All candidates have recent recs within 10h → should return (False, reason)."""
    cards = sqlite3.connect(db_with_cards).execute("SELECT id, player_name, version_name, card_key FROM cards").fetchall()
    card_id, name, vname, ckey = cards[0]

    # Add a recent recommendation for this card
    con = sqlite3.connect(db_with_cards)
    con.execute(
        "INSERT INTO recommendations (card_id, platform, call, confidence, source) VALUES (?,?,?,?,?)",
        (card_id, "pc", "buy", 80.0, "llm_autonomous"),
    )
    con.commit()
    con.close()

    candidates = [{"card_id": card_id, "player_name": name, "version_name": vname, "card_key": ckey}]
    should_run, reason = _has_worthy_candidates(candidates, "pc", db_with_cards)
    assert should_run is False
    assert "already have recent" in reason


def test_has_worthy_candidates_returns_true_with_fresh_candidate(db_with_cards):
    """A candidate with no recent rec and some signals → (True, reason)."""
    cards = sqlite3.connect(db_with_cards).execute("SELECT id, player_name, version_name, card_key FROM cards").fetchall()
    card_id, name, vname, ckey = cards[0]

    # Add a recent signal so signal_count > 0
    # Use T-format timestamp to match the strftime query in _has_worthy_candidates
    con = sqlite3.connect(db_with_cards)
    con.execute(
        "INSERT INTO signals (source, ts_utc, signal_type, raw_text) VALUES (?,strftime('%Y-%m-%dT%H:%M:%SZ','now'),'tweet','test signal')",
        ("twitter",),
    )
    con.commit()
    con.close()

    candidates = [{"card_id": card_id, "player_name": name, "version_name": vname, "card_key": ckey}]
    should_run, reason = _has_worthy_candidates(candidates, "pc", db_with_cards)
    assert should_run is True
    assert "candidates ready" in reason


def test_has_worthy_candidates_no_signals_too_few_candidates(tmp_db):
    """0 signals AND < 3 candidates → (False, reason)."""
    # Two candidates, no signals
    con = sqlite3.connect(tmp_db)
    for i in range(2):
        con.execute(
            "INSERT INTO cards (card_key, player_name, version_name, game_edition) VALUES (?,?,?,?)",
            (f"card-{i}", f"Player{i}", "TOTS", "fc26"),
        )
    con.commit()
    cards = con.execute("SELECT id, player_name, version_name, card_key FROM cards").fetchall()
    con.close()

    candidates = [{"card_id": r[0], "player_name": r[1], "version_name": r[2], "card_key": r[3]} for r in cards]
    should_run, reason = _has_worthy_candidates(candidates, "pc", tmp_db)
    assert should_run is False
    assert "No recent signals" in reason


# ---------------------------------------------------------------------------
# _get_budget_status
# ---------------------------------------------------------------------------

def test_get_budget_status_shape_when_empty(tmp_db):
    """Budget status returns correct shape and zero spent when no calls logged."""
    status = _get_budget_status(tmp_db)
    assert "spent_today_usd" in status
    assert "cap_usd" in status
    assert "remaining_usd" in status
    assert "can_generate" in status
    assert status["spent_today_usd"] == 0.0
    assert status["cap_usd"] == 0.02
    assert status["remaining_usd"] == 0.02
    assert status["can_generate"] is True


def test_get_budget_status_exhausted(tmp_db):
    """Budget status shows can_generate=False when spend >= cap."""
    con = sqlite3.connect(tmp_db)
    con.execute(
        "INSERT INTO llm_calls (model, input_tokens, output_tokens, cost_usd, feature) VALUES (?,?,?,?,?)",
        ("claude-haiku-4-5-20251001", 1000, 200, 0.025, "autonomous"),
    )
    con.commit()
    con.close()

    status = _get_budget_status(tmp_db)
    assert status["can_generate"] is False
    assert status["remaining_usd"] == 0.0
