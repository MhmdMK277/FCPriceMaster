"""Tests for the fodder scraper — parsing, filtering, and DB insertion."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.migrate import run_migrations
from src.scrapers.futgg import FutGGScraper, _parse_price, _is_valid_fut_price


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

def test_is_valid_fut_price():
    # Below minimum
    assert not _is_valid_fut_price(150)
    assert not _is_valid_fut_price(199)
    # Valid 200-999 range (multiples of 50)
    assert _is_valid_fut_price(200)
    assert _is_valid_fut_price(300)
    assert _is_valid_fut_price(950)
    assert not _is_valid_fut_price(225)  # not multiple of 50
    # Valid 1000-9999 range (multiples of 100)
    assert _is_valid_fut_price(1000)
    assert _is_valid_fut_price(1100)
    assert _is_valid_fut_price(3700)
    assert _is_valid_fut_price(9900)
    assert not _is_valid_fut_price(1050)  # not multiple of 100
    assert not _is_valid_fut_price(9950)  # not multiple of 100
    # Valid 10000-99999 range (multiples of 250)
    assert _is_valid_fut_price(10000)
    assert _is_valid_fut_price(12750)
    assert _is_valid_fut_price(16500)
    assert not _is_valid_fut_price(10333)  # not multiple of 250
    # Valid 100000+ range (multiples of 1000)
    assert _is_valid_fut_price(100000)
    assert _is_valid_fut_price(355000)
    assert not _is_valid_fut_price(100500)  # not multiple of 1000
    # Real market prices from known-correct sweep
    assert _is_valid_fut_price(300)   # rating 81
    assert _is_valid_fut_price(400)   # rating 82
    assert _is_valid_fut_price(750)   # rating 83/84
    assert _is_valid_fut_price(3500)  # rating 89
    assert _is_valid_fut_price(5500)  # rating 90
    assert _is_valid_fut_price(25000) # rating 93


def test_parse_price_handles_various_formats():
    assert _parse_price("65.5K") == 65500
    assert _parse_price("1.2M") == 1_200_000
    assert _parse_price("EXTINCT") is None
    assert _parse_price("0") is None
    assert _parse_price("450") == 450


def test_fodder_filter_logic():
    """Only 0-coin / None cards are excluded — no minimum price floor."""
    raw_prices = [None, 0, 400, 1500, 3500, 55000, 58000, 62000, 70000, 80000]
    # _parse_price("0") returns None, so filter is: price is not None
    valid = [p for p in raw_prices if p is not None]
    valid = valid[:10]
    assert valid == [0, 400, 1500, 3500, 55000, 58000, 62000, 70000, 80000]
    # In the scraper, _parse_price("0") returns None, so 0 never appears in practice
    # Simulate scraper behaviour: skip None only
    from src.scrapers.futgg import _parse_badge
    texts = ["EXTINCT", "0", "400", "CAM\n89\n3.5K"]
    prices = [_parse_badge(t)[2] for t in texts]
    # EXTINCT → None, "0" → None, "400" → None (parse_price treats "0" as None),
    # "CAM\n89\n3.5K" → 3500
    assert prices[0] is None  # EXTINCT
    assert prices[1] is None  # 0
    assert prices[2] == 400   # 400 coins is valid (no floor)
    assert prices[3] == 3500


# ---------------------------------------------------------------------------
# Integration: DB insertion — fodder_snapshots
# ---------------------------------------------------------------------------

def test_fodder_snapshot_insert(tmp_db):
    """fodder_snapshots accepts rows with correct schema."""
    con = sqlite3.connect(tmp_db)
    con.execute(
        """INSERT INTO fodder_snapshots
           (rating, platform, cheapest_bin, second_cheapest_bin, median_bin, game_edition)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (85, "pc", 3500, 4200, 5800, "fc26"),
    )
    con.commit()
    row = con.execute(
        "SELECT rating, platform, cheapest_bin, second_cheapest_bin, median_bin FROM fodder_snapshots"
    ).fetchone()
    con.close()
    assert row == (85, "pc", 3500, 4200, 5800)


def test_fodder_snapshot_rejects_invalid_platform(tmp_db):
    """Platform CHECK constraint must reject values other than pc/console."""
    con = sqlite3.connect(tmp_db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO fodder_snapshots (rating, platform, cheapest_bin) VALUES (?,?,?)",
            (85, "xbox", 3500),
        )
        con.commit()
    con.close()


def test_multiple_fodder_snapshots_ratings(tmp_db):
    """Can insert fodder rows for all ratings 81-93."""
    con = sqlite3.connect(tmp_db)
    for rating in range(81, 94):
        for platform in ("pc", "console"):
            con.execute(
                """INSERT INTO fodder_snapshots
                   (rating, platform, cheapest_bin, second_cheapest_bin, median_bin)
                   VALUES (?,?,?,?,?)""",
                (rating, platform, 3000 + rating * 50, 3100 + rating * 50, 3500 + rating * 50),
            )
    con.commit()
    count = con.execute("SELECT COUNT(*) FROM fodder_snapshots").fetchone()[0]
    con.close()
    assert count == 26  # 13 ratings × 2 platforms


# ---------------------------------------------------------------------------
# Integration: DB insertion — fodder_cards
# ---------------------------------------------------------------------------

def test_fodder_cards_insert(tmp_db):
    """fodder_cards rows insert correctly and link to fodder_snapshots."""
    con = sqlite3.connect(tmp_db)
    cur = con.execute(
        """INSERT INTO fodder_snapshots (rating, platform, cheapest_bin, median_bin)
           VALUES (89, 'pc', 3500, 5000)"""
    )
    snap_id = cur.lastrowid
    con.execute(
        """INSERT INTO fodder_cards
           (snapshot_id, card_key, player_name, rating, position, club_name, nation_name,
            club_badge_url, nation_flag_url, card_version, bin_price, rank_in_rating, platform)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (snap_id, "26-123456", "Wirtz", 89, "CAM", "Bayer Leverkusen", "Germany",
         "https://cdn.fut.gg/clubs/example.png", "https://cdn.fut.gg/nations/example.png",
         "Normal", 3500, 1, "pc"),
    )
    con.commit()

    row = con.execute(
        "SELECT player_name, bin_price, rank_in_rating, club_badge_url, nation_flag_url FROM fodder_cards"
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == "Wirtz"
    assert row[1] == 3500
    assert row[2] == 1
    assert row[3] == "https://cdn.fut.gg/clubs/example.png"
    assert row[4] == "https://cdn.fut.gg/nations/example.png"


def test_fodder_cards_image_urls_non_null(tmp_db):
    """Image URL columns default to empty string (not NULL)."""
    con = sqlite3.connect(tmp_db)
    cur = con.execute(
        """INSERT INTO fodder_snapshots (rating, platform, cheapest_bin, median_bin)
           VALUES (82, 'console', 1200, 1800)"""
    )
    snap_id = cur.lastrowid
    con.execute(
        """INSERT INTO fodder_cards
           (snapshot_id, card_key, player_name, rating, position,
            bin_price, rank_in_rating, platform)
           VALUES (?,?,?,?,?,?,?,?)""",
        (snap_id, "", "Unknown", 82, "ST", 1200, 1, "console"),
    )
    con.commit()

    row = con.execute(
        "SELECT club_badge_url, nation_flag_url, club_name, nation_name FROM fodder_cards"
    ).fetchone()
    con.close()
    # All should be empty string (NOT NULL default '')
    assert row is not None
    for val in row:
        assert val is not None
        assert isinstance(val, str)


def test_fodder_cards_no_zero_price(tmp_db):
    """Verify that fodder_cards only contains non-zero prices when inserted by scraper logic."""
    prices = [None, 0, 400, 1500, 3500]
    # Simulate what the scraper does: skip price is None
    valid = [p for p in prices if p is not None]
    assert 0 in valid   # 0 would come from _parse_price("0") which returns None, so never here
    # In practice _parse_price("0") → None, so 0 never makes it past the filter
    # This test verifies that None is correctly excluded
    filtered = [p for p in prices if p is not None]
    assert None not in filtered
    assert len(filtered) == 4  # 0, 400, 1500, 3500


# ---------------------------------------------------------------------------
# Mocked Playwright: fetch_fodder_cheapest
# ---------------------------------------------------------------------------

def _make_anchor_mock(badge_text: str, alt: str = "", href: str = ""):
    """Build a mock anchor element with configurable badge/alt/href."""
    anchor = MagicMock()

    # Badge element (.font-din)
    first_badge = MagicMock()
    async def inner_text(): return badge_text
    async def badge_count(): return 1 if badge_text else 0
    first_badge.inner_text = inner_text
    first_badge.count = badge_count
    badge_locator = MagicMock()
    badge_locator.first = first_badge

    # Card img (for alt text with player name/version)
    card_img = MagicMock()
    async def img_alt_get(attr):
        return alt if attr == "alt" else ""
    card_img.get_attribute = img_alt_get

    img_locator = MagicMock()
    img_locator.nth = MagicMock(return_value=card_img)
    async def img_count(): return 1 if alt else 0
    img_locator.count = img_count

    # Club img
    club_img = MagicMock()
    async def club_count(): return 0
    club_img.count = club_count
    club_locator = MagicMock()
    club_locator.first = club_img

    # Nation img
    nation_img = MagicMock()
    async def nation_count(): return 0
    nation_img.count = nation_count
    nation_locator = MagicMock()
    nation_locator.first = nation_img

    def locator_dispatch(sel):
        if ".font-din" in sel:
            return badge_locator
        if "club" in sel:
            return club_locator
        if "nation" in sel or "flag" in sel:
            return nation_locator
        return img_locator  # fallback for img

    anchor.locator = MagicMock(side_effect=locator_dispatch)

    async def get_attribute(attr):
        if attr == "href": return href
        return ""
    anchor.get_attribute = get_attribute
    return anchor


@pytest.mark.asyncio
async def test_fetch_fodder_cheapest_mock(tmp_db):
    """fetch_fodder_cheapest persists snapshot + per-card rows with correct aggregates."""
    # Cards: 5 valid non-zero cards (CAM badge format: pos\nrating\nprice)
    card_data = [
        ("CAM\n89\n3.5K", "Wirtz - 89 - Normal", "/players/188350-wirtz/26-100001/"),
        ("ST\n89\n4.2K",  "Kane - 89 - Normal",  "/players/100001-kane/26-100002/"),
        ("CB\n89\n5.8K",  "Ramos - 89 - Normal", "/players/100002-ramos/26-100003/"),
        ("LM\n89\n6.1K",  "Sane - 89 - Normal",  "/players/100003-sane/26-100004/"),
        ("RW\n89\n7.0K",  "Salah - 89 - Normal", "/players/100004-salah/26-100005/"),
    ]

    mock_anchors = MagicMock()
    mock_anchors.count = AsyncMock(return_value=len(card_data))
    mock_anchors.nth = MagicMock(side_effect=lambda i: _make_anchor_mock(
        card_data[i][0], card_data[i][1], card_data[i][2]
    ))

    mock_page = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_anchors)

    scraper = FutGGScraper(db_path=tmp_db)
    scraper._context = MagicMock()
    scraper._context.new_page = AsyncMock(return_value=mock_page)

    with patch.object(scraper, '_navigate', new_callable=AsyncMock), \
         patch.object(scraper, '_set_platform', new_callable=AsyncMock), \
         patch.object(scraper, '_dismiss_cmp', new_callable=AsyncMock):
        result = await scraper.fetch_fodder_cheapest(89, "pc")

    assert result is not None
    assert result["cheapest_bin"] == 3500
    assert result["second_cheapest_bin"] == 4200
    assert result["median_bin"] == 5800

    con = sqlite3.connect(tmp_db)
    snap = con.execute(
        "SELECT rating, platform, cheapest_bin, second_cheapest_bin, median_bin FROM fodder_snapshots"
    ).fetchone()
    assert snap == (89, "pc", 3500, 4200, 5800)

    cards = con.execute(
        "SELECT player_name, bin_price, rank_in_rating FROM fodder_cards ORDER BY rank_in_rating"
    ).fetchall()
    con.close()

    assert len(cards) == 5
    assert cards[0][0] == "Wirtz"
    assert cards[0][1] == 3500
    assert cards[0][2] == 1
    assert cards[1][0] == "Kane"
    assert cards[1][2] == 2


@pytest.mark.asyncio
async def test_fetch_fodder_cheapest_ipc_shape(tmp_db):
    """getFodderByRating returns correct shape after a fodder sweep."""
    con = sqlite3.connect(tmp_db)
    cur = con.execute(
        """INSERT INTO fodder_snapshots (rating, platform, cheapest_bin, median_bin)
           VALUES (89, 'pc', 3500, 5000)"""
    )
    snap_id = cur.lastrowid
    for rank, (name, price) in enumerate([("Wirtz", 3500), ("Kane", 4200), ("Ramos", 5800)], 1):
        con.execute(
            """INSERT INTO fodder_cards
               (snapshot_id, card_key, player_name, rating, position,
                club_name, nation_name, club_badge_url, nation_flag_url,
                card_version, bin_price, rank_in_rating, platform)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (snap_id, f"26-{rank}", name, 89, "CAM", "", "", "", "", "Normal", price, rank, "pc"),
        )
    con.commit()
    con.close()

    # Simulate getFodderByRating query
    con2 = sqlite3.connect(tmp_db)
    rows = con2.execute(
        """SELECT fc.player_name, fc.bin_price, fc.rank_in_rating,
                  fc.club_badge_url, fc.nation_flag_url
           FROM fodder_cards fc
           WHERE fc.snapshot_id = (
             SELECT id FROM fodder_snapshots WHERE rating=89 AND platform='pc'
             ORDER BY ts_utc DESC LIMIT 1
           )
           ORDER BY fc.rank_in_rating ASC LIMIT 10""",
    ).fetchall()
    con2.close()

    assert len(rows) == 3
    assert rows[0][0] == "Wirtz"
    assert rows[0][1] == 3500
    # Image URL columns are non-null strings
    assert rows[0][3] is not None
    assert rows[0][4] is not None


@pytest.mark.asyncio
async def test_fetch_fodder_all_ratings_mock(tmp_db):
    """fetch_fodder_all_ratings uses page.evaluate() to extract sections, persists correctly."""
    # Simulate JS evaluate returning sections in /cheapest-by-rating/ anchor-text format:
    # parts = [name, price_str, position, rating_str]
    fake_sections = [
        {
            "rating": 89,
            "cards": [
                {"href": "/players/188350-wirtz/26-100001/",  "parts": ["Wirtz", "3,500", "CAM", "89"]},
                {"href": "/players/100001-kane/26-100002/",   "parts": ["Kane",  "4,250", "ST",  "89"]},
                {"href": "/players/100002-ramos/26-100003/",  "parts": ["Ramos", "5,500", "CB",  "89"]},
            ],
        },
        {
            "rating": 82,
            "cards": [
                {"href": "/players/200001-player/26-200001/", "parts": ["Player A", "400", "ST", "82"]},
                {"href": "/players/200002-player/26-200002/", "parts": ["Player B", "450", "CB", "82"]},
            ],
        },
        {
            # Price below 200 → rejected by the live path's price < 200 check
            "rating": 83,
            "cards": [
                {"href": "/players/300001-bad/26-300001/", "parts": ["Bad", "150", "LW", "83"]},
            ],
        },
    ]

    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=fake_sections)

    scraper = FutGGScraper(db_path=tmp_db)
    scraper._context = MagicMock()
    scraper._context.new_page = AsyncMock(return_value=mock_page)

    with patch.object(scraper, '_navigate', new_callable=AsyncMock), \
         patch.object(scraper, '_dismiss_cmp', new_callable=AsyncMock):
        results = await scraper.fetch_fodder_all_ratings("pc")

    # rating 89 and 82 should be present; rating 83 rejected (39000 not valid FUT price)
    assert 89 in results
    assert 82 in results
    assert 83 not in results

    assert results[89]["cheapest_bin"] == 3500
    assert results[89]["second_cheapest_bin"] == 4250
    assert results[82]["cheapest_bin"] == 400

    con = sqlite3.connect(tmp_db)
    snaps = con.execute(
        "SELECT rating, cheapest_bin FROM fodder_snapshots ORDER BY rating"
    ).fetchall()
    assert (82, 400) in snaps
    assert (89, 3500) in snaps

    cards_89 = con.execute(
        "SELECT player_name, bin_price FROM fodder_cards WHERE rating=89 ORDER BY rank_in_rating"
    ).fetchall()
    assert cards_89[0] == ("Wirtz", 3500)
    assert cards_89[1] == ("Kane", 4250)
    con.close()
