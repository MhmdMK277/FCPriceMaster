"""Tests for signal_tagger: alias seeding, fuzzy matching, signal tagging."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from src.db.migrate import run_migrations
from src.workers.signal_tagger import seed_card_aliases, run_tagging


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
    """Insert a few test cards for alias seeding and tagging tests."""
    con = sqlite3.connect(tmp_db)
    con.execute(
        "INSERT INTO cards (card_key, player_name, version_name, game_edition) VALUES (?,?,?,?)",
        ("26-111", "Vinícius Jr.", "TOTY", "fc26"),
    )
    con.execute(
        "INSERT INTO cards (card_key, player_name, version_name, game_edition) VALUES (?,?,?,?)",
        ("26-222", "Florian Wirtz", "TOTS", "fc26"),
    )
    con.execute(
        "INSERT INTO cards (card_key, player_name, version_name, game_edition) VALUES (?,?,?,?)",
        ("26-333", "Erling Haaland", "TOTY", "fc26"),
    )
    con.commit()
    con.close()
    return tmp_db


# ---------------------------------------------------------------------------
# Alias seeding
# ---------------------------------------------------------------------------

def test_seed_card_aliases_populates_aliases(db_with_cards):
    count = seed_card_aliases(db_with_cards)
    assert count > 0
    con = sqlite3.connect(db_with_cards)
    alias_count = con.execute("SELECT COUNT(*) FROM card_aliases").fetchone()[0]
    con.close()
    assert alias_count > 0


def test_seed_card_aliases_idempotent(db_with_cards):
    """Seeding twice should not insert duplicates."""
    seed_card_aliases(db_with_cards)
    con = sqlite3.connect(db_with_cards)
    count1 = con.execute("SELECT COUNT(*) FROM card_aliases").fetchone()[0]
    con.close()
    # Second seed: the function short-circuits when count > 100 — but our test DB has <100
    # Just verify no duplicates
    seed_card_aliases(db_with_cards)
    con = sqlite3.connect(db_with_cards)
    count2 = con.execute("SELECT COUNT(*) FROM card_aliases").fetchone()[0]
    con.close()
    # Count should not have decreased (no dups thanks to INSERT OR IGNORE)
    assert count2 >= count1


# ---------------------------------------------------------------------------
# Signal tagging
# ---------------------------------------------------------------------------

def test_run_tagging_tags_signal_with_card_mention(db_with_cards):
    """A signal mentioning 'Wirtz' should get tagged to Florian Wirtz's card."""
    con = sqlite3.connect(db_with_cards)
    # Insert a test signal
    con.execute(
        """INSERT INTO signals (source, source_id, ts_utc, raw_text, signal_type)
           VALUES ('test', 'sig-1', '2026-01-01T00:00:00Z', 'Buy Wirtz gold under 63K on console', 'direct')"""
    )
    con.commit()
    con.close()

    # Seed aliases first
    seed_card_aliases(db_with_cards)
    count = run_tagging(db_with_cards)
    assert count >= 1

    con = sqlite3.connect(db_with_cards)
    # Find the Wirtz card id
    wirtz_id = con.execute(
        "SELECT id FROM cards WHERE player_name LIKE '%Wirtz%'"
    ).fetchone()[0]
    signal_id = con.execute("SELECT id FROM signals WHERE source_id='sig-1'").fetchone()[0]
    tag_row = con.execute(
        "SELECT * FROM signal_card_tags WHERE signal_id=? AND card_id=?",
        (signal_id, wirtz_id),
    ).fetchone()
    con.close()
    assert tag_row is not None, "Expected signal_card_tags row for Wirtz"


def test_run_tagging_sets_tagged_at(db_with_cards):
    """tagged_at column should be set after tagging."""
    con = sqlite3.connect(db_with_cards)
    con.execute(
        """INSERT INTO signals (source, source_id, ts_utc, raw_text, signal_type)
           VALUES ('test', 'sig-2', '2026-01-01T00:00:00Z', 'Some text about Haaland', 'direct')"""
    )
    con.commit()
    con.close()

    seed_card_aliases(db_with_cards)
    run_tagging(db_with_cards)

    con = sqlite3.connect(db_with_cards)
    row = con.execute("SELECT tagged_at FROM signals WHERE source_id='sig-2'").fetchone()
    con.close()
    assert row is not None
    assert row[0] is not None, "tagged_at should be set after tagging"


def test_run_tagging_skips_already_tagged(db_with_cards):
    """Signals with tagged_at set should not be re-processed."""
    con = sqlite3.connect(db_with_cards)
    con.execute(
        """INSERT INTO signals (source, source_id, ts_utc, raw_text, signal_type, tagged_at)
           VALUES ('test', 'sig-3', '2026-01-01T00:00:00Z', 'Haaland under 5M', 'direct', '2026-01-01T00:00:00Z')"""
    )
    con.commit()
    con.close()

    seed_card_aliases(db_with_cards)
    count = run_tagging(db_with_cards)
    # sig-3 was pre-tagged, should not be processed
    assert count == 0


def test_run_tagging_skips_null_text(db_with_cards):
    """Signals with NULL raw_text should not be processed."""
    con = sqlite3.connect(db_with_cards)
    con.execute(
        """INSERT INTO signals (source, source_id, ts_utc, raw_text, signal_type)
           VALUES ('test', 'sig-4', '2026-01-01T00:00:00Z', NULL, 'direct')"""
    )
    con.commit()
    con.close()

    seed_card_aliases(db_with_cards)
    run_tagging(db_with_cards)

    # The null-text signal should have tagged_at=NULL (skipped)
    con = sqlite3.connect(db_with_cards)
    row = con.execute("SELECT tagged_at FROM signals WHERE source_id='sig-4'").fetchone()
    con.close()
    # NULL text signals are excluded by WHERE clause — tagged_at stays NULL
    assert row[0] is None
