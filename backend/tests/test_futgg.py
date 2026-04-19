"""Tests for FUT.GG scraper — schema guard and parsing logic.

These tests monkey-patch Playwright page interactions so they run offline.
They verify: correct parsing, schema guard firing on bad data, and
scraper_health rows being written on both success and failure paths.
"""

from __future__ import annotations

import sqlite3
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.scrapers.futgg import (
    FutGGScraper,
    _parse_price,
    _parse_badge,
    _card_key_from_href,
    _player_name_from_alt,
)
from src.scrapers.base import SchemaGuardError
from src.db.migrate import run_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db():
    """Create a fresh in-memory-ish DB file for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    run_migrations(path)
    yield path
    # On Windows, SQLite WAL files may still be held briefly; ignore cleanup errors
    try:
        os.unlink(path)
        for suffix in ("-wal", "-shm"):
            wal = path + suffix
            if os.path.exists(wal):
                os.unlink(wal)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Pure parsing unit tests (no Playwright, no DB)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("355.6K", 355600),
    ("1.2M", 1_200_000),
    ("355,150", 355150),
    ("43K", 43000),
    ("0.4K", 400),
    ("EXTINCT", None),
    ("N/A", None),
    ("", None),
    ("-", None),
])
def test_parse_price(raw, expected):
    assert _parse_price(raw) == expected


@pytest.mark.parametrize("badge,expected", [
    ("CAM\n93.0\n355.6K", ("CAM", 93.0, 355600)),
    ("GK\n92.5\n43.3K", ("GK", 92.5, 43300)),
    ("CM\n90.1\nEXTINCT", ("CM", 90.1, None)),
    ("355,550", ("", None, 355550)),
])
def test_parse_badge(badge, expected):
    assert _parse_badge(badge) == expected


def test_card_key_from_href():
    assert _card_key_from_href("/players/188350-marco-reus/26-67297214/") == "26-67297214"
    assert _card_key_from_href("/players/new/") is None


def test_player_name_from_alt():
    name, version = _player_name_from_alt("Reus - 93 - TOTS HM")
    assert name == "Reus"
    assert version == "TOTS HM"


# ---------------------------------------------------------------------------
# Integration test: happy path — monkey-patched Playwright responses
# ---------------------------------------------------------------------------

def _async_mock_with_count(count_val: int, **extra_attrs):
    """Return an AsyncMock with .count() returning count_val and .first returning self."""
    m = AsyncMock()
    m.count = AsyncMock(return_value=count_val)
    m.first = m  # .first returns same mock
    for k, v in extra_attrs.items():
        setattr(m, k, AsyncMock(return_value=v))
    return m


def _make_mock_anchor(href: str, alt: str, badge: str):
    """Build a mock Playwright locator representing one card anchor."""
    anchor = AsyncMock()
    anchor.get_attribute = AsyncMock(side_effect=lambda attr, **kw: href if attr == "href" else None)

    img_mock = _async_mock_with_count(1, get_attribute=alt)
    badge_mock = _async_mock_with_count(1, inner_text=badge)

    def locator_side_effect(sel, **kw):
        if "img" in sel:
            return img_mock
        if "font-din" in sel:
            return badge_mock
        return _async_mock_with_count(0)

    anchor.locator = MagicMock(side_effect=locator_side_effect)
    return anchor


@pytest.mark.asyncio
async def test_fetch_hot_cards_happy_path(tmp_db):
    """
    Simulate a trending page with 2 cards. Verify:
    - Both cards inserted into DB
    - 2 price snapshots written
    - scraper_health row with success=1
    """
    cards_data = [
        ("/players/188350-marco-reus/26-67297214/", "Reus - 93 - TOTS HM", "CAM\n93.0\n355.6K"),
        ("/players/200104-heung-min-son/26-84086184/", "Son - 93 - TOTS", "LM\n93.1\n523.3K"),
    ]

    scraper = FutGGScraper(db_path=tmp_db)

    # Build mock page
    mock_page = AsyncMock()
    mock_page.close = AsyncMock()

    anchors_locator = MagicMock()
    anchors_locator.count = AsyncMock(return_value=2)
    anchors_locator.nth = MagicMock(side_effect=lambda i: _make_mock_anchor(*cards_data[i]))

    def locator_side_effect(sel, **kw):
        if 'group/player' in sel or 'group\\/player' in sel:
            return anchors_locator
        if '/26-' in sel:
            return anchors_locator
        return MagicMock()

    mock_page.locator = MagicMock(side_effect=locator_side_effect)

    with (
        patch.object(scraper, '_new_page', return_value=mock_page),
        patch.object(scraper, '_navigate', new_callable=AsyncMock),
        patch.object(scraper, '_set_platform', new_callable=AsyncMock),
    ):
        result = await scraper.fetch_hot_cards(platform="console", limit=2)

    assert len(result) == 2
    assert result[0]["card_key"] == "26-67297214"
    assert result[1]["bin_price"] == 523300

    # Check DB
    con = sqlite3.connect(tmp_db)
    card_count = con.execute("SELECT COUNT(*) FROM cards WHERE game_edition='fc26'").fetchone()[0]
    snap_count = con.execute("SELECT COUNT(*) FROM price_snapshots WHERE platform='console'").fetchone()[0]
    health = con.execute(
        "SELECT success, records_written FROM scraper_health WHERE source='futgg' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()

    assert card_count == 2
    assert snap_count == 2
    assert health is not None
    assert health[0] == 1  # success
    assert health[1] == 2  # records_written


# ---------------------------------------------------------------------------
# Integration test: schema guard fires on bad data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_hot_cards_schema_guard_on_bad_response(tmp_db):
    """
    If a card anchor has no href (card_key cannot be extracted),
    the card is skipped (no SchemaGuardError raised for partial data).
    If ALL cards fail validation, success count is 0 but health row is still written.
    """
    # Return an anchor whose href doesn't match the expected pattern
    bad_anchor = MagicMock()
    bad_anchor.get_attribute = AsyncMock(return_value="/players/new/")  # no card_id
    bad_anchor.locator = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=0)))

    scraper = FutGGScraper(db_path=tmp_db)
    mock_page = AsyncMock()
    mock_page.close = AsyncMock()

    anchors_locator = MagicMock()
    anchors_locator.count = AsyncMock(return_value=1)
    anchors_locator.nth = MagicMock(return_value=bad_anchor)

    mock_page.locator = MagicMock(return_value=anchors_locator)

    with (
        patch.object(scraper, '_new_page', return_value=mock_page),
        patch.object(scraper, '_navigate', new_callable=AsyncMock),
        patch.object(scraper, '_set_platform', new_callable=AsyncMock),
    ):
        result = await scraper.fetch_hot_cards(platform="pc", limit=1)

    # Unparseable anchor is silently skipped
    assert result == []

    # Health row should still exist (written at end of fetch_hot_cards)
    con = sqlite3.connect(tmp_db)
    health = con.execute(
        "SELECT success, records_written FROM scraper_health WHERE source='futgg' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert health is not None
    assert health[0] == 1  # success=True (run completed, 0 valid cards)
    assert health[1] == 0


@pytest.mark.asyncio
async def test_schema_guard_error_raised_directly(tmp_db):
    """validate() raises SchemaGuardError if a required field is missing."""
    scraper = FutGGScraper(db_path=tmp_db)
    with pytest.raises(SchemaGuardError, match="Missing fields"):
        scraper.validate({"card_key": "26-123", "player_name": "Test"})  # missing version_name, platform
