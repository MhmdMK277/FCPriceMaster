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

_FODDER_SOURCE = "futgg_fodder"

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

    # ------------------------------------------------------------------
    # Fodder tracking
    # ------------------------------------------------------------------

    async def fetch_fodder_cheapest(self, rating: int, platform: Platform) -> dict[str, Any] | None:
        """
        Navigate to the cheapest-by-rating FUT.GG list, collect the top 10 cheapest
        non-zero-price cards, store per-card rows in fodder_cards, and store
        aggregate (cheapest/second_cheapest/median) in fodder_snapshots.

        Platform is passed as a URL parameter — avoids the Radix dropdown which
        times out on /players/?sort=cheapest pages.

        Only cards with price == 0 (extinct/delisted) are excluded.  All other
        cards regardless of version/rarity are valid fodder data.

        Returns the snapshot dict (including `cards` list), or None if no valid
        cards were found.
        """
        plat_param = "pc" if platform == "pc" else "console"
        # Use overall__gte/lte to pin exact rating; sorts=current_price gives cheapest-first order.
        # The &platform= URL param avoids the Radix dropdown click entirely.
        url = (
            f"https://www.fut.gg/players/"
            f"?overall__gte={rating}&overall__lte={rating}"
            f"&sorts=current_price&platform={plat_param}"
        )
        page = await self._new_page()
        try:
            await self._navigate(page, url)
            try:
                await page.wait_for_selector('a[href*="/players/"][href*="/26-"]', timeout=60000)
            except Exception:
                pass

            anchors = page.locator('a[href*="/players/"][href*="/26-"]')
            count = await anchors.count()
            count = min(count, 30)  # examine up to 30 to collect 10 valid

            cards: list[dict[str, Any]] = []
            for i in range(count):
                anchor = anchors.nth(i)

                # Badge text → position, rating, price
                badge_el = anchor.locator(".font-din").first
                if await badge_el.count() == 0:
                    continue
                badge_text = await badge_el.inner_text()
                position, card_rating_f, price = _parse_badge(badge_text)
                if price is None:  # 0-coin / extinct / missing — skip
                    continue

                # href → card_key
                href = await anchor.get_attribute("href") or ""
                card_key = _card_key_from_href(href) or ""

                # Card art img alt → player name, card version
                card_imgs = anchor.locator("img")
                player_name = ""
                card_version = ""
                img_count = await card_imgs.count()
                for j in range(img_count):
                    alt = await card_imgs.nth(j).get_attribute("alt") or ""
                    # FUT.GG alt format: "Wirtz - 89 - Normal" or "Reus - 93 - TOTS HM"
                    if " - " in alt and any(c.isdigit() for c in alt):
                        player_name, card_version = _player_name_from_alt(alt)
                        break

                # Club badge: img whose src contains 'club'
                club_badge_url = ""
                club_name = ""
                club_img = anchor.locator("img[src*='club']").first
                if await club_img.count() > 0:
                    club_badge_url = await club_img.get_attribute("src") or ""
                    club_name = await club_img.get_attribute("alt") or ""

                # Nation flag: img whose src contains 'nation' or 'flag'
                nation_flag_url = ""
                nation_name = ""
                nation_img = anchor.locator("img[src*='nation'], img[src*='flag']").first
                if await nation_img.count() > 0:
                    nation_flag_url = await nation_img.get_attribute("src") or ""
                    nation_name = await nation_img.get_attribute("alt") or ""

                card_rating = int(card_rating_f) if card_rating_f is not None else rating

                cards.append({
                    "card_key": card_key,
                    "player_name": player_name,
                    "rating": card_rating,
                    "position": position,
                    "club_name": club_name,
                    "nation_name": nation_name,
                    "club_badge_url": club_badge_url,
                    "nation_flag_url": nation_flag_url,
                    "card_version": card_version,
                    "bin_price": price,
                })
                if len(cards) >= 10:
                    break

            if not cards:
                logger.warning(
                    "fetch_fodder_cheapest(rating=%d, platform=%s): no valid prices found",
                    rating, platform,
                )
                _write_health(
                    self.db_path, _FODDER_SOURCE, success=False,
                    error_text=f"No valid prices for rating={rating} platform={platform}",
                )
                return None

            prices_sorted = sorted(c["bin_price"] for c in cards)
            cheapest = prices_sorted[0]
            second_cheapest = prices_sorted[1] if len(prices_sorted) > 1 else None
            median = prices_sorted[len(prices_sorted) // 2]

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            con = sqlite3.connect(self.db_path)
            with con:
                cur = con.execute(
                    """INSERT INTO fodder_snapshots
                       (rating, platform, ts_utc, cheapest_bin, second_cheapest_bin, median_bin, game_edition)
                       VALUES (?,?,?,?,?,?,?)""",
                    (rating, platform, ts, cheapest, second_cheapest, median, "fc26"),
                )
                snapshot_id = cur.lastrowid
                for rank, card in enumerate(cards, start=1):
                    con.execute(
                        """INSERT INTO fodder_cards
                           (snapshot_id, card_key, player_name, rating, position,
                            club_name, nation_name, club_badge_url, nation_flag_url,
                            card_version, bin_price, rank_in_rating, ts_utc, platform, game_edition)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            snapshot_id, card["card_key"], card["player_name"], card["rating"],
                            card["position"], card["club_name"], card["nation_name"],
                            card["club_badge_url"], card["nation_flag_url"], card["card_version"],
                            card["bin_price"], rank, ts, platform, "fc26",
                        ),
                    )

            _write_health(self.db_path, _FODDER_SOURCE, success=True, records_written=len(cards))
            logger.info(
                "fodder snapshot: rating=%d platform=%s cheapest=%d median=%d cards=%d",
                rating, platform, cheapest, median, len(cards),
            )
            return {
                "rating": rating,
                "platform": platform,
                "cheapest_bin": cheapest,
                "second_cheapest_bin": second_cheapest,
                "median_bin": median,
                "ts_utc": ts,
                "cards": cards,
            }

        except Exception as exc:
            _write_health(self.db_path, _FODDER_SOURCE, success=False, error_text=str(exc))
            raise
        finally:
            await page.close()

    async def fetch_fodder_all_ratings(
        self,
        platform: Platform,
        ratings: list[int] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """
        Navigate to https://www.fut.gg/cheapest-by-rating/ once and extract cheapest
        cards for every rating section visible on the page.  One page load per platform
        instead of 13.

        Returns {rating: snapshot_dict} for every rating that yielded at least one card.
        Falls back to fetch_fodder_cheapest per-rating on any unrecoverable error.
        """
        ratings = ratings or list(range(81, 94))
        plat_param = "pc" if platform == "pc" else "console"
        url = f"https://www.fut.gg/cheapest-by-rating/?platform={plat_param}"
        page = await self._new_page()
        results: dict[int, dict[str, Any]] = {}
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            await self._navigate(page, url)
            # Wait for at least one card anchor to appear
            try:
                await page.wait_for_selector('a[href*="/players/"][href*="/26-"]', timeout=60000)
            except Exception:
                pass

            # The page has rating sections.  Each section has a heading that includes the
            # rating number (e.g. "Cheapest 89 Rated Players") and then a row of card anchors.
            # We iterate over all card anchors and group by their rating badge value.
            anchors = page.locator('a[href*="/players/"][href*="/26-"]')
            total_anchors = await anchors.count()
            total_anchors = min(total_anchors, 300)  # cap to avoid runaway

            # Per-rating card accumulator
            rating_cards: dict[int, list[dict[str, Any]]] = {}

            for i in range(total_anchors):
                anchor = anchors.nth(i)

                badge_el = anchor.locator(".font-din").first
                if await badge_el.count() == 0:
                    continue
                badge_text = await badge_el.inner_text()
                position, card_rating_f, price = _parse_badge(badge_text)
                if price is None or card_rating_f is None:
                    continue

                card_rating = int(card_rating_f)
                if card_rating not in ratings:
                    continue

                bucket = rating_cards.setdefault(card_rating, [])
                if len(bucket) >= 10:
                    continue  # already have 10 for this rating

                href = await anchor.get_attribute("href") or ""
                card_key = _card_key_from_href(href) or ""

                card_imgs = anchor.locator("img")
                player_name = ""
                card_version = ""
                img_count = await card_imgs.count()
                for j in range(img_count):
                    alt = await card_imgs.nth(j).get_attribute("alt") or ""
                    if " - " in alt and any(c.isdigit() for c in alt):
                        player_name, card_version = _player_name_from_alt(alt)
                        break

                club_badge_url = ""
                club_name = ""
                club_img = anchor.locator("img[src*='club']").first
                if await club_img.count() > 0:
                    club_badge_url = await club_img.get_attribute("src") or ""
                    club_name = await club_img.get_attribute("alt") or ""

                nation_flag_url = ""
                nation_name = ""
                nation_img = anchor.locator("img[src*='nation'], img[src*='flag']").first
                if await nation_img.count() > 0:
                    nation_flag_url = await nation_img.get_attribute("src") or ""
                    nation_name = await nation_img.get_attribute("alt") or ""

                bucket.append({
                    "card_key": card_key,
                    "player_name": player_name,
                    "rating": card_rating,
                    "position": position,
                    "club_name": club_name,
                    "nation_name": nation_name,
                    "club_badge_url": club_badge_url,
                    "nation_flag_url": nation_flag_url,
                    "card_version": card_version,
                    "bin_price": price,
                })

            # Persist each rating bucket
            con = sqlite3.connect(self.db_path)
            with con:
                for card_rating, cards in rating_cards.items():
                    if not cards:
                        continue
                    prices_sorted = sorted(c["bin_price"] for c in cards)
                    cheapest = prices_sorted[0]
                    second_cheapest = prices_sorted[1] if len(prices_sorted) > 1 else None
                    median = prices_sorted[len(prices_sorted) // 2]

                    cur = con.execute(
                        """INSERT INTO fodder_snapshots
                           (rating, platform, ts_utc, cheapest_bin, second_cheapest_bin, median_bin, game_edition)
                           VALUES (?,?,?,?,?,?,?)""",
                        (card_rating, platform, ts, cheapest, second_cheapest, median, "fc26"),
                    )
                    snapshot_id = cur.lastrowid
                    for rank, card in enumerate(cards, start=1):
                        con.execute(
                            """INSERT INTO fodder_cards
                               (snapshot_id, card_key, player_name, rating, position,
                                club_name, nation_name, club_badge_url, nation_flag_url,
                                card_version, bin_price, rank_in_rating, ts_utc, platform, game_edition)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                snapshot_id, card["card_key"], card["player_name"], card["rating"],
                                card["position"], card["club_name"], card["nation_name"],
                                card["club_badge_url"], card["nation_flag_url"], card["card_version"],
                                card["bin_price"], rank, ts, platform, "fc26",
                            ),
                        )

                    results[card_rating] = {
                        "rating": card_rating,
                        "platform": platform,
                        "cheapest_bin": cheapest,
                        "second_cheapest_bin": second_cheapest,
                        "median_bin": median,
                        "ts_utc": ts,
                        "cards": cards,
                    }
                    logger.info(
                        "fodder_all_ratings: rating=%d platform=%s cheapest=%d cards=%d",
                        card_rating, platform, cheapest, len(cards),
                    )

            records = sum(len(v["cards"]) for v in results.values())
            _write_health(self.db_path, _FODDER_SOURCE, success=True, records_written=records)
            return results

        except Exception as exc:
            _write_health(self.db_path, _FODDER_SOURCE, success=False, error_text=str(exc))
            raise
        finally:
            await page.close()

    async def fodder_sweep(
        self,
        ratings: list[int] | None = None,
        platforms: list[Platform] | None = None,
    ) -> int:
        """
        Sweep all rating+platform combos using fetch_fodder_all_ratings (1 page load per
        platform instead of 13).  Falls back to per-rating fetch_fodder_cheapest if the
        all-ratings page yields no results for a platform.

        Returns total snapshot rows inserted.
        """
        ratings = ratings or list(range(81, 94))
        platforms = platforms or ["pc", "console"]
        total = 0
        for plat in platforms:
            try:
                results = await self.fetch_fodder_all_ratings(plat, ratings)
                total += len(results)
                if results:
                    logger.info(
                        "fodder_sweep (all-ratings): platform=%s ratings_found=%d", plat, len(results)
                    )
                    continue
            except Exception as exc:
                logger.warning(
                    "fetch_fodder_all_ratings failed for platform=%s: %s — falling back to per-rating", plat, exc
                )

            # Fallback: per-rating fetches
            logger.info("fodder_sweep: falling back to per-rating fetches for platform=%s", plat)
            for rating in ratings:
                try:
                    result = await self.fetch_fodder_cheapest(rating, plat)
                    if result:
                        total += 1
                except Exception as exc:
                    logger.error(
                        "fodder_sweep fallback failed for rating=%d platform=%s: %s", rating, plat, exc
                    )
        return total

    async def fetch_card_on_demand(self, card_key: str, platform: Platform, max_age_hours: float = 2.0) -> dict[str, Any] | None:
        """
        Fetch a fresh price for card_key/platform only if the latest snapshot is
        older than max_age_hours (or absent). Called by the signal tagger so that
        any mentioned card has fresh price data when Ask LLM is called.

        Returns the snapshot dict from fetch_card_prices, or None if skipped / card absent.
        """
        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff_str = (cutoff - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        con = sqlite3.connect(self.db_path)
        row = con.execute(
            """SELECT id FROM cards WHERE card_key = ?""", (card_key,)
        ).fetchone()
        if not row:
            con.close()
            return None
        card_id = row[0]

        fresh = con.execute(
            """SELECT id FROM price_snapshots
               WHERE card_id = ? AND platform = ? AND ts_utc >= ?
               LIMIT 1""",
            (card_id, platform, cutoff_str),
        ).fetchone()
        con.close()

        if fresh:
            logger.debug("fetch_card_on_demand: %s/%s is fresh, skipping", card_key, platform)
            return None

        logger.info("fetch_card_on_demand: fetching %s/%s (stale/absent)", card_key, platform)
        try:
            return await self.fetch_card_prices(card_key, platform)
        except Exception as exc:
            logger.warning("fetch_card_on_demand failed for %s/%s: %s", card_key, platform, exc)
            return None

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
    parser.add_argument("--fodder", action="store_true", help="Run fodder sweep instead of trending")
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
        if args.fodder:
            print(f"Running fodder sweep — platform={args.platform}, ratings 81-93")
            total = await scraper.fodder_sweep(
                ratings=list(range(81, 94)),
                platforms=[args.platform],
            )
            print(f"Fodder snapshots inserted: {total}")
            import sqlite3 as _sq
            con = _sq.connect(db_path)
            rows = con.execute(
                """SELECT rating, cheapest_bin, second_cheapest_bin, median_bin, ts_utc
                   FROM fodder_snapshots WHERE platform=?
                   ORDER BY rating""",
                (args.platform,),
            ).fetchall()
            con.close()
            print(f"\nFodder table (platform={args.platform}):")
            print(f"  {'Rating':<8} {'Cheapest':<12} {'2nd':<12} {'Median':<12} {'Updated'}")
            for r in rows:
                print(f"  {r[0]:<8} {(r[1] or 0):>10,}  {(r[2] or 0):>10,}  {(r[3] or 0):>10,}  {r[4]}")
            return

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
