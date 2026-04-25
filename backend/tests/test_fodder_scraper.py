"""Tests for the fodder scraper — parsing, filtering, and DB insertion."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.migrate import run_migrations
from src.scrapers.futgg import FutGGScraper, _parse_price


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


# ---------------------------------------------------------------------------
# Unit: filtering logic
# ---------------------------------------------------------------------------

def test_parse_price_handles_various_formats():
    assert _parse_price("65.5K") == 65500
    assert _parse_price("1.2M") == 1_200_000
    assert _parse_price("EXTINCT") is None
    assert _parse_price("0") is None
    assert _parse_price("450") == 450  # troll listing value (filtering done elsewhere)


def test_fodder_filter_logic():
    """Verify the 0-coin and <500-coin filter used in fetch_fodder_cheapest."""
    raw_prices = [None, 400, 0, 55000, 58000, 62000, 70000, 80000]
    valid = [p for p in raw_prices if p and p >= 500]
    valid = valid[:5]
    assert valid == [55000, 58000, 62000, 70000, 80000]
    cheapest = valid[0]
    second = valid[1] if len(valid) > 1 else None
    median = valid[len(valid) // 2] if valid else None
    assert cheapest == 55000
    assert second == 58000
    assert median == 62000


# ---------------------------------------------------------------------------
# Integration: DB insertion
# ---------------------------------------------------------------------------

def test_fodder_snapshot_insert(tmp_db):
    """Verify fodder_snapshots table accepts rows with the correct schema."""
    con = sqlite3.connect(tmp_db)
    con.execute(
        """INSERT INTO fodder_snapshots
           (rating, platform, cheapest_bin, second_cheapest_bin, median_bin, game_edition)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (85, "pc", 55000, 58000, 62000, "fc26"),
    )
    con.commit()
    row = con.execute(
        "SELECT rating, platform, cheapest_bin, second_cheapest_bin, median_bin FROM fodder_snapshots"
    ).fetchone()
    con.close()
    assert row == (85, "pc", 55000, 58000, 62000)


def test_fodder_snapshot_rejects_invalid_platform(tmp_db):
    """Platform CHECK constraint must reject values other than pc/console."""
    con = sqlite3.connect(tmp_db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO fodder_snapshots (rating, platform, cheapest_bin) VALUES (?,?,?)",
            (85, "xbox", 55000),
        )
        con.commit()
    con.close()


def test_multiple_fodder_snapshots_ratings(tmp_db):
    """Verify we can insert fodder rows for all ratings 82-91."""
    con = sqlite3.connect(tmp_db)
    for rating in range(82, 92):
        for platform in ("pc", "console"):
            con.execute(
                """INSERT INTO fodder_snapshots
                   (rating, platform, cheapest_bin, second_cheapest_bin, median_bin)
                   VALUES (?,?,?,?,?)""",
                (rating, platform, 50000 + rating * 100, 51000 + rating * 100, 52000 + rating * 100),
            )
    con.commit()
    count = con.execute("SELECT COUNT(*) FROM fodder_snapshots").fetchone()[0]
    con.close()
    assert count == 20  # 10 ratings × 2 platforms


# ---------------------------------------------------------------------------
# Mocked Playwright: fetch_fodder_cheapest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_fodder_cheapest_mock(tmp_db):
    """Verify fetch_fodder_cheapest persists a row with correct aggregates."""
    from src.scrapers.futgg import FutGGScraper

    # Build mock badge elements returning prices: 57K, 59K, 63K, 71K, 85K
    # first element is troll (400 coins) and one is None — should be filtered
    badge_texts = ["", "400", "57K", "59K", "63K", "71K", "85K"]

    mock_anchors = MagicMock()
    mock_anchors.count = AsyncMock(return_value=len(badge_texts))

    mock_page = AsyncMock()
    mock_page.locator = MagicMock()

    def make_nth_anchor(i):
        anchor = MagicMock()
        # Playwright: anchor.locator(".font-din").first.count() and .inner_text()
        first_el = MagicMock()
        async def inner_text():
            return badge_texts[i]
        async def count():
            return 1 if badge_texts[i] else 0
        first_el.inner_text = inner_text
        first_el.count = count
        locator_mock = MagicMock()
        locator_mock.first = first_el
        anchor.locator = MagicMock(return_value=locator_mock)
        return anchor

    mock_page.locator.return_value = mock_anchors
    mock_anchors.nth = MagicMock(side_effect=make_nth_anchor)

    scraper = FutGGScraper(db_path=tmp_db)
    scraper._context = MagicMock()
    scraper._context.new_page = AsyncMock(return_value=mock_page)

    with patch.object(scraper, '_navigate', new_callable=AsyncMock), \
         patch.object(scraper, '_set_platform', new_callable=AsyncMock), \
         patch.object(scraper, '_dismiss_cmp', new_callable=AsyncMock):
        await scraper.fetch_fodder_cheapest(85, "pc")

    con = sqlite3.connect(tmp_db)
    row = con.execute(
        "SELECT rating, platform, cheapest_bin, second_cheapest_bin, median_bin FROM fodder_snapshots"
    ).fetchone()
    con.close()
    # Expected: filter out None, 400 -> valid=[57000,59000,63000,71000,85000]
    # cheapest=57000, second=59000, median=63000
    assert row is not None
    assert row[0] == 85
    assert row[1] == "pc"
    assert row[2] == 57000
    assert row[3] == 59000
    assert row[4] == 63000
