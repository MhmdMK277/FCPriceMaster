"""FUT.GG scraper — Playwright DOM scraping of public pages only.

Strategy: navigate public FUT.GG pages as a real browser user, let JS render,
read prices from the rendered DOM. We do NOT intercept or hit /api/* endpoints
directly. The browser makes those XHR calls internally; we only read what is
rendered on screen.

Public pages used:
  /players/trending/           — hot card list (trending by FUT.GG's algorithm)
  /players/{pid}-{slug}/{edition}-{cid}/  — individual card detail
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from .base import PlaywrightScraperBase, SchemaGuardError, _write_health

logger = logging.getLogger(__name__)

Platform = Literal["pc", "console"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HREF_RE = re.compile(r"/players/(\d+)-([^/]+)/(\d+)-(\d+)/$")


def _parse_price(raw: str) -> int | None:
    """
    Convert FUT.GG price strings to integer coin value.
    '355.6K' → 355600, '1.2M' → 1200000, '355,150' → 355150,
    'EXTINCT' | 'N/A' | '' → None
    """
    raw = raw.strip()
    if not raw or raw.upper() in ("EXTINCT", "N/A", "-", "0"):
        return None
    # Remove commas
    raw = raw.replace(",", "")
    try:
        if raw.upper().endswith("M"):
            return round(float(raw[:-1]) * 1_000_000)
        if raw.upper().endswith("K"):
            return round(float(raw[:-1]) * 1_000)
        return round(float(raw))
    except ValueError:
        return None


def _parse_badge(badge_text: str) -> tuple[str, float | None, int | None]:
    """
    Parse the card footer badge text.
    Formats seen:
      'CAM\n93.0\n355.6K'   → (position, rating, price)
      'CAM\n93.0\nEXTINCT'  → (position, rating, None)
      '355,550'              → ('', None, price)   ← detail page main card
    Returns (position, rating, bin_price).
    """
    parts = [p.strip() for p in badge_text.strip().split("\n") if p.strip()]
    if len(parts) == 3:
        position = parts[0]
        try:
            rating = float(parts[1])
        except ValueError:
            rating = None
        price = _parse_price(parts[2])
        return position, rating, price
    if len(parts) == 1:
        # Detail page main card shows just the price number
        return "", None, _parse_price(parts[0])
    return "", None, None


def _card_key_from_href(href: str) -> str | None:
    """'/players/188350-marco-reus/26-67297214/' → '26-67297214'"""
    m = _HREF_RE.search(href)
    if not m:
        return None
    edition, card_id = m.group(3), m.group(4)
    return f"{edition}-{card_id}"


def _player_name_from_alt(alt: str) -> tuple[str, str]:
    """
    img alt is 'Reus - 93 - TOTS HM' → (player_name, version_name)
    Falls back to ('Unknown', 'Unknown') if unparseable.
    """
    parts = [p.strip() for p in alt.split(" - ")]
    if len(parts) >= 3:
        return parts[0], " ".join(parts[2:])
    if len(parts) == 2:
        return parts[0], parts[1]
    return alt, "Unknown"


def _db_path_from_env() -> str:
    import os
    here = os.path.dirname(__file__)
    return os.path.normpath(os.path.join(here, "..", "..", "..", "data", "fcpricemaster.db"))


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _upsert_card(con: sqlite3.Connection, card_key: str, player_name: str, version_name: str) -> int:
    """Insert card if not exists, return its id."""
    con.execute(
        "INSERT OR IGNORE INTO cards (card_key, player_name, version_name, game_edition) VALUES (?,?,?,?)",
        (card_key, player_name, version_name, "fc26"),
    )
    row = con.execute("SELECT id FROM cards WHERE card_key=?", (card_key,)).fetchone()
    return row[0]


def _upsert_attributes(con: sqlite3.Connection, card_id: int, attrs: dict[str, str]) -> None:
    for key, value in attrs.items():
        if value:
            con.execute(
                "INSERT OR IGNORE INTO card_attributes (card_id, key, value) VALUES (?,?,?)",
                (card_id, key, value),
            )


def _insert_snapshot(con: sqlite3.Connection, card_id: int, platform: str, bin_price: int | None) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute(
        """INSERT INTO price_snapshots (card_id, platform, game_edition, ts_utc, bin_price, source)
           VALUES (?,?,?,?,?,?)""",
        (card_id, platform, "fc26", ts, bin_price, "futgg"),
    )


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class FutGGScraper(PlaywrightScraperBase):
    SOURCE = "futgg"

    def expected_schema(self) -> dict[str, type]:
        return {
            "card_key": str,
            "player_name": str,
            "version_name": str,
            "platform": str,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_hot_cards(self, platform: Platform, limit: int = 500) -> list[dict[str, Any]]:
        """
        Navigate /players/trending/, switch to the requested platform,
        extract all visible card entries and persist to DB.
        Returns list of card dicts with basic attributes.
        """
        page = await self._new_page()
        try:
            await self._navigate(page, "https://www.fut.gg/players/trending/")
            # domcontentloaded fires before JS renders cards; wait for at least one card anchor.
            try:
                await page.wait_for_selector('a[href*="/players/"][href*="/26-"]', timeout=90000)
            except Exception:
                pass  # proceed anyway; count check below handles empty page gracefully
            await self._set_platform(page, platform)

            # Card anchors: try the CSS class FUT.GG uses, fall back to href pattern
            anchors = page.locator('a[href*="/players/"][href*="/26-"]')
            count = await anchors.count()

            logger.info("Found %d card anchors on trending page (platform=%s)", count, platform)
            count = min(count, limit)

            results: list[dict[str, Any]] = []
            con = sqlite3.connect(self.db_path)

            with con:
                for i in range(count):
                    anchor = anchors.nth(i)
                    href = await anchor.get_attribute("href") or ""
                    card_key = _card_key_from_href(href)
                    if not card_key:
                        continue

                    # Get alt text from the card image for name/version
                    img = anchor.locator("img").first
                    alt = ""
                    if await img.count() > 0:
                        alt = await img.get_attribute("alt") or ""
                    player_name, version_name = _player_name_from_alt(alt)

                    # Badge: position, rating, price
                    badge_el = anchor.locator(".font-din").first
                    badge_text = ""
                    if await badge_el.count() > 0:
                        badge_text = await badge_el.inner_text()
                    position, rating, bin_price = _parse_badge(badge_text)

                    card_data: dict[str, Any] = {
                        "card_key": card_key,
                        "player_name": player_name,
                        "version_name": version_name,
                        "platform": platform,
                        "position": position,
                        "rating": rating,
                        "bin_price": bin_price,
                        "href": href,
                    }
                    try:
                        self.validate(card_data)
                    except SchemaGuardError as exc:
                        logger.warning("Schema guard: %s for %s", exc, href)
                        continue

                    card_id = _upsert_card(con, card_key, player_name, version_name)
                    attrs: dict[str, str] = {}
                    if position:
                        attrs["position"] = position
                    if rating is not None:
                        attrs["rating"] = str(rating)
                    _upsert_attributes(con, card_id, attrs)
                    _insert_snapshot(con, card_id, platform, bin_price)
                    results.append(card_data)

            _write_health(self.db_path, self.SOURCE, success=True, records_written=len(results))
            logger.info("fetch_hot_cards(%s): %d cards persisted", platform, len(results))
            return results

        except Exception as exc:
            _write_health(self.db_path, self.SOURCE, success=False, error_text=str(exc))
            raise
        finally:
            await page.close()

    async def fetch_card_prices(self, card_key: str, platform: Platform) -> dict[str, Any] | None:
        """
        Navigate the card's detail page, switch to the requested platform,
        extract and persist the current BIN price.
        Returns a dict with card_key, platform, bin_price, or None if card not found in DB.
        """
        # Look up the card in the DB to get its href slug
        con = sqlite3.connect(self.db_path)
        row = con.execute(
            "SELECT id, player_name, version_name FROM cards WHERE card_key=?", (card_key,)
        ).fetchone()
        con.close()
        if not row:
            logger.warning("fetch_card_prices: card_key %s not in DB; run fetch_hot_cards first", card_key)
            return None

        card_id, player_name, version_name = row

        # Reconstruct detail URL from card_key "26-67297214"
        # We need to find the full slug. Use a search approach:
        # Navigate to a search URL or use the known href from a previous hot_cards run.
        # For now, look up href from recent snapshot source data isn't stored.
        # Use the FUT.GG player search URL pattern instead.
        # card_key = "26-{card_id_int}" → detail page is /players/*/{card_key}/
        # We search for it via the players page
        edition, card_id_str = card_key.split("-", 1)
        detail_url = f"https://www.fut.gg/players/-/{card_key}/"

        page = await self._new_page()
        try:
            # Navigate to a known working pattern: search for the card
            # The actual URL requires the player slug. Use the search endpoint.
            search_url = f"https://www.fut.gg/players/?search={card_id_str}"
            await self._navigate(page, search_url)
            await self._set_platform(page, platform)

            # Find the card link
            anchor = page.locator(f'a[href*="/{card_key}/"]').first
            anchor_count = await anchor.count()

            if anchor_count == 0:
                logger.warning("Card %s not found on search page", card_key)
                _write_health(self.db_path, self.SOURCE, success=False,
                              error_text=f"Card {card_key} not found via search")
                return None

            href = await anchor.get_attribute("href") or ""
            full_url = f"https://www.fut.gg{href}"
            await self._navigate(page, full_url)

            # Get main card price badge (first .fc-card-container .font-din)
            badge_el = page.locator(".fc-card-container .font-din").first
            await badge_el.wait_for(timeout=10000)
            badge_text = await badge_el.inner_text()
            _, _, bin_price = _parse_badge(badge_text)

            con = sqlite3.connect(self.db_path)
            with con:
                db_row = con.execute("SELECT id FROM cards WHERE card_key=?", (card_key,)).fetchone()
                if db_row:
                    _insert_snapshot(con, db_row[0], platform, bin_price)

            result = {"card_key": card_key, "platform": platform, "bin_price": bin_price}
            _write_health(self.db_path, self.SOURCE, success=True, records_written=1)
            return result

        except Exception as exc:
            _write_health(self.db_path, self.SOURCE, success=False, error_text=str(exc))
            raise
        finally:
            await page.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    parser = argparse.ArgumentParser(description="FUT.GG scraper (manual run)")
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--platform", choices=["pc", "console"], default="console")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = _db_path_from_env()

    # Ensure DB + schema exist
    from src.db.migrate import run_migrations
    run_migrations(db_path)

    async with FutGGScraper(db_path=db_path) as scraper:
        print(f"Scraping trending cards — platform={args.platform}, limit={args.limit}")
        cards = await scraper.fetch_hot_cards(platform=args.platform, limit=args.limit)
        print(f"Cards fetched: {len(cards)}")
        for c in cards:
            price_str = f"{c['bin_price']:,}" if c['bin_price'] else "EXTINCT"
            print(f"  {c['card_key']:<15}  {c['player_name']:<25}  {c['version_name']:<12}  "
                  f"{c.get('position',''):<4} {c.get('rating','-')}  {price_str}")

    # Summary from DB
    import sqlite3 as _sq
    con = _sq.connect(db_path)
    card_count = con.execute("SELECT COUNT(*) FROM cards WHERE game_edition='fc26'").fetchone()[0]
    snap_count = con.execute(
        "SELECT COUNT(*) FROM price_snapshots WHERE platform=? AND source='futgg'", (args.platform,)
    ).fetchone()[0]
    health = con.execute(
        "SELECT success, records_written, last_error FROM scraper_health WHERE source='futgg' ORDER BY run_at_utc DESC LIMIT 1"
    ).fetchone()
    con.close()

    print(f"\nDB summary:")
    print(f"  cards (fc26):            {card_count}")
    print(f"  snapshots ({args.platform}): {snap_count}")
    if health:
        status = "OK" if health[0] else "FAILED"
        print(f"  scraper_health:         {status}, rows_written={health[1]}, error={health[2]}")


if __name__ == "__main__":
    asyncio.run(_main())
