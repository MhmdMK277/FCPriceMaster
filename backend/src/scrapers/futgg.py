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

# Rating bands for the daily full card sweep (fetch_all_cards_paginated)
SWEEP_BANDS: list[tuple[int, int]] = [(78, 81), (82, 84), (85, 87), (88, 90), (91, 93), (94, 99)]

# Shared list-page extraction (used by fetch_cards_by_rating and the paginated sweep).
# Returns [{href, badgeText, alt, isUntradeable}] for up to maxExamine card anchors.
_LIST_EXTRACT_JS = """(maxExamine) => {
  var anchors = document.querySelectorAll('a[href*="/players/"][href*="/26-"]');
  var results = [];
  for (var i = 0; i < anchors.length && i < maxExamine; i++) {
    var a = anchors[i];
    var href = a.getAttribute('href') || '';

    // Badge: .font-din contains "POS\\nRATING\\nPRICE"
    var badge = a.querySelector('.font-din');
    var badgeText = badge ? badge.innerText : '';

    // Card image alt: "PlayerName - Rating - Version"
    var imgs = a.querySelectorAll('img[alt]');
    var alt = '';
    for (var j = 0; j < imgs.length; j++) {
      var a2 = imgs[j].getAttribute('alt') || '';
      if (a2.indexOf(' - ') >= 0 && /\\d/.test(a2)) { alt = a2; break; }
    }

    // Untradeable detection: lock element or text
    var isUntradeable = false;
    var lockEl = a.querySelector('[class*="lock"],[data-tradeable="false"],[aria-label*="tradeable"],[aria-label*="Tradeable"]');
    if (lockEl) isUntradeable = true;
    if (!isUntradeable) {
      var txt = a.innerText.toLowerCase();
      if (txt.indexOf('non-tradeable') >= 0 || txt.indexOf('not tradeable') >= 0) isUntradeable = true;
    }

    results.push({href: href, badgeText: badgeText, alt: alt, isUntradeable: isUntradeable});
  }
  return results;
}"""

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


def _is_valid_fut_price(price: int) -> bool:
    """
    Reject prices that cannot be valid FUT market bids.
    Actual EA FC price ladder (observed from live market):
      200–999: multiples of 50
      1000–9999: multiples of 100
      10000–99999: multiples of 250
      100000+: multiples of 1000
    """
    if price < 200:
        return False
    if price < 1000:
        return price % 50 == 0
    if price < 10000:
        return price % 100 == 0
    if price < 100000:
        return price % 250 == 0
    return price % 1000 == 0


_UNTRADEABLE_VERSION_RE = re.compile(r"\b(SBC|Objectives?)\b", re.IGNORECASE)


def _is_real_bin_price(price: int | None, version_name: str = "") -> bool:
    """
    True only if `price` can be a real transfer-market BIN.
    Untradeable cards (SBC/Objective rewards) show their SBC cost estimate on
    FUT.GG list pages — those values do not follow the FUT increment ladder.
    """
    if price is None or price <= 0:
        return False
    if version_name and _UNTRADEABLE_VERSION_RE.search(version_name):
        return False
    return _is_valid_fut_price(price)


def _classify_tradeable(bin_price: int | None, version_name: str) -> int | None:
    """
    Classify a list-page card from its displayed price.
      0    — untradeable evidence (SBC/Objective version, or price breaks the
             FUT increment ladder → it's an SBC cost estimate, not a BIN)
      1    — verified real BIN price → card provably trades
      None — no evidence either way (EXTINCT / missing price; tradeable cards
             can be extinct, so absence of a price never proves untradeable)
    """
    if version_name and _UNTRADEABLE_VERSION_RE.search(version_name):
        return 0
    if bin_price is None:
        return None
    return 1 if _is_real_bin_price(bin_price, version_name) else 0


async def _page_is_tradeable(page: Any) -> bool:
    """
    Detail-page tradeability check (the authoritative signal on FUT.GG):
    tradeable cards have a "Prices" nav tab and a Lowest BIN / Price Momentum
    section; untradeable (SBC/Objective) cards have neither — the coin value
    next to the card art is the SBC cost estimate.
    Returns True on evaluation failure (never flip tradeable without evidence).
    """
    js = """() => {
        const tabs = Array.from(document.querySelectorAll('a, button, [role="tab"]'));
        const hasPricesTab = tabs.some(el => el.textContent?.trim() === 'Prices');
        const hasLowestBin = !!document.querySelector(
            '[class*="lowest-bin"], [class*="LowestBin"], [class*="price-momentum"], [class*="PriceMomentum"]'
        );
        return hasPricesTab || hasLowestBin;
    }"""
    try:
        return bool(await page.evaluate(js))
    except Exception:
        return True


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

def _upsert_card(
    con: sqlite3.Connection,
    card_key: str,
    player_name: str,
    version_name: str,
    tradeable: int | None = None,
) -> int:
    """Insert card if not exists, return its id.

    tradeable=0 forces the flag off; tradeable=1 restores it (a verified real
    BIN price proves the card trades, recovering cards the 30-day staleness
    migration retired); None leaves an existing card's flag untouched.
    """
    con.execute(
        "INSERT OR IGNORE INTO cards (card_key, player_name, version_name, game_edition, tradeable) VALUES (?,?,?,?,?)",
        (card_key, player_name, version_name, "fc26", 1 if tradeable is None else tradeable),
    )
    if tradeable is not None:
        con.execute("UPDATE cards SET tradeable=? WHERE card_key=?", (tradeable, card_key))
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

                    tradeable_flag = _classify_tradeable(bin_price, version_name)
                    card_id = _upsert_card(con, card_key, player_name, version_name, tradeable=tradeable_flag)
                    attrs: dict[str, str] = {}
                    if position:
                        attrs["position"] = position
                    if rating is not None:
                        attrs["rating"] = str(rating)
                    _upsert_attributes(con, card_id, attrs)
                    if tradeable_flag == 0:
                        # SBC cost estimate or SBC/Objective version — not a market price
                        logger.debug(
                            "fetch_hot_cards: %s (%s) marked untradeable — price %s is not a real BIN",
                            player_name, version_name, bin_price,
                        )
                        continue
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
    # Rating-tier card coverage
    # ------------------------------------------------------------------

    async def fetch_cards_by_rating(
        self,
        rating_min: int,
        rating_max: int,
        platform: Platform,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Navigate /players/?overall__gte={min}&overall__lte={max}&sorts=current_price&platform={p},
        extract up to `limit` cards sorted cheapest-first, upsert into cards + card_attributes,
        insert price_snapshots for tradeable cards.

        Non-tradeable cards (lock icon or "non-tradeable" text detected) are upserted with
        tradeable=0 and receive no price snapshot. They are excluded from the return list.

        Returns list of dicts for tradeable cards only.
        """
        plat_param = "pc" if platform == "pc" else "console"
        url = (
            f"https://www.fut.gg/players/"
            f"?overall__gte={rating_min}&overall__lte={rating_max}"
            f"&sorts=current_price&platform={plat_param}"
        )
        source = f"futgg_tier"
        page = await self._new_page()
        try:
            await self._navigate(page, url)
            try:
                await page.wait_for_selector('a[href*="/players/"][href*="/26-"]', timeout=60000)
            except Exception:
                pass

            # Prices load via XHR after the initial DOM renders, so badges initially show only
            # "POS\nRATING" (2 parts). Wait until at least one badge has a numeric price (3 parts,
            # third part not EXTINCT/N/A/LEVEL). Fall through if all cards are EXTINCT/untradeable.
            try:
                await page.wait_for_function(
                    """() => {
                        var badges = document.querySelectorAll('a[href*="/26-"] .font-din');
                        for (var i = 0; i < badges.length; i++) {
                            var lines = badges[i].innerText.trim().split('\\n')
                                .map(function(l){return l.trim();}).filter(Boolean);
                            if (lines.length >= 3) {
                                var p = lines[2].toUpperCase();
                                if (p !== 'EXTINCT' && p !== 'N/A' && p.indexOf('LEVEL') < 0) return true;
                            }
                        }
                        return false;
                    }""",
                    timeout=12000,
                )
            except Exception:
                pass  # all cards may be EXTINCT/non-tradeable; proceed to extract what we have

            # Single JS pass — fetch up to limit*5 anchors; Python post-filters to limit valid prices.
            # Many cards on FUT.GG sorted pages are EXTINCT or non-tradeable evolutions so we
            # over-fetch and discard those without a valid numeric price.
            max_examine = min(limit * 5, 500)
            raw_cards: list[dict] = await page.evaluate(_LIST_EXTRACT_JS, max_examine)

            if not raw_cards:
                logger.warning(
                    "fetch_cards_by_rating(%d-%d, %s): JS evaluate returned no cards",
                    rating_min, rating_max, platform,
                )
                _write_health(self.db_path, source, success=False,
                              error_text=f"No cards for {rating_min}-{rating_max} {platform}")
                return []

            results: list[dict[str, Any]] = []
            con = sqlite3.connect(self.db_path)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            with con:
                for raw in raw_cards:
                    href = raw.get("href", "")
                    card_key = _card_key_from_href(href)
                    if not card_key:
                        continue

                    # Parse badge → position, rating, price
                    badge_text = raw.get("badgeText", "")
                    position, rating_f, bin_price = _parse_badge(badge_text)

                    # Parse alt → player_name, version_name
                    alt = raw.get("alt", "")
                    player_name, version_name = _player_name_from_alt(alt) if alt else ("Unknown", "Unknown")

                    is_untradeable = bool(raw.get("isUntradeable", False))

                    # Combine the JS lock-icon flag with price-based classification:
                    # an invalid-increment price is an SBC cost estimate → untradeable.
                    tradeable_flag = 0 if is_untradeable else _classify_tradeable(bin_price, version_name)

                    card_id = _upsert_card(con, card_key, player_name, version_name, tradeable=tradeable_flag)

                    attrs: dict[str, str] = {}
                    if position:
                        attrs["position"] = position
                    if rating_f is not None:
                        attrs["rating"] = str(int(rating_f))
                    _upsert_attributes(con, card_id, attrs)

                    if tradeable_flag == 1:
                        con.execute(
                            """INSERT INTO price_snapshots
                               (card_id, platform, game_edition, ts_utc, bin_price, source)
                               VALUES (?,?,?,?,?,?)""",
                            (card_id, platform, "fc26", ts, bin_price, "futgg_tier"),
                        )
                        results.append({
                            "card_key": card_key,
                            "player_name": player_name,
                            "version_name": version_name,
                            "platform": platform,
                            "position": position,
                            "rating": int(rating_f) if rating_f is not None else None,
                            "bin_price": bin_price,
                            "tradeable": 1,
                        })
                        if len(results) >= limit:
                            break

            _write_health(self.db_path, source, success=True, records_written=len(results))
            logger.info(
                "fetch_cards_by_rating(%d-%d, %s): %d tradeable cards persisted",
                rating_min, rating_max, platform, len(results),
            )
            return results

        except Exception as exc:
            _write_health(self.db_path, source, success=False, error_text=str(exc))
            raise
        finally:
            await page.close()

    async def fetch_all_cards_paginated(
        self,
        platform: Platform,
        min_rating: int,
        max_rating: int,
    ) -> dict[str, int]:
        """
        Page through ALL cards in a rating band on FUT.GG /players/ (daily full
        sweep — discovers cards the tier scraper's first-page limit never sees).
        Returns {found, new, updated, skipped_untradeable}.

        Only stores snapshots for cards passing _is_real_bin_price().
        Rate limit: 3-6s jitter between pages. Stops after two consecutive
        empty pages or 50 pages (safety limit).
        """
        import random

        source = f"futgg_sweep_{min_rating}_{max_rating}_{platform}"
        plat_param = "pc" if platform == "pc" else "console"
        results = {"found": 0, "new": 0, "updated": 0, "skipped_untradeable": 0}
        seen_keys: set[str] = set()
        page_num = 1
        consecutive_empty = 0

        page = await self._new_page()
        try:
            con = sqlite3.connect(self.db_path)
            while page_num <= 50 and consecutive_empty < 2:
                url = (
                    f"https://www.fut.gg/players/"
                    f"?overall__gte={min_rating}&overall__lte={max_rating}"
                    f"&sorts=current_price&platform={plat_param}&page={page_num}"
                )
                await self._navigate(page, url)
                try:
                    await page.wait_for_selector('a[href*="/players/"][href*="/26-"]', timeout=30000)
                except Exception:
                    pass

                raw_cards: list[dict] = await page.evaluate(_LIST_EXTRACT_JS, 500)
                page_found = 0
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                with con:
                    for raw in raw_cards:
                        card_key = _card_key_from_href(raw.get("href", ""))
                        if not card_key or card_key in seen_keys:
                            continue
                        seen_keys.add(card_key)
                        page_found += 1

                        position, rating_f, bin_price = _parse_badge(raw.get("badgeText", ""))
                        alt = raw.get("alt", "")
                        player_name, version_name = _player_name_from_alt(alt) if alt else ("Unknown", "Unknown")

                        is_new = con.execute(
                            "SELECT 1 FROM cards WHERE card_key=?", (card_key,)
                        ).fetchone() is None

                        tradeable_flag = (
                            0 if raw.get("isUntradeable")
                            else _classify_tradeable(bin_price, version_name)
                        )
                        card_id = _upsert_card(con, card_key, player_name, version_name, tradeable=tradeable_flag)

                        attrs: dict[str, str] = {}
                        if position:
                            attrs["position"] = position
                        if rating_f is not None:
                            attrs["rating"] = str(int(rating_f))
                        _upsert_attributes(con, card_id, attrs)

                        if is_new:
                            results["new"] += 1
                        if tradeable_flag == 0:
                            results["skipped_untradeable"] += 1
                        elif tradeable_flag == 1:
                            con.execute(
                                """INSERT INTO price_snapshots
                                   (card_id, platform, game_edition, ts_utc, bin_price, source)
                                   VALUES (?,?,?,?,?,?)""",
                                (card_id, platform, "fc26", ts, bin_price, "futgg_sweep"),
                            )
                            results["updated"] += 1

                results["found"] += page_found
                if page_found == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0
                logger.info(
                    "fetch_all_cards_paginated(%d-%d, %s): page %d — %d cards (totals: %s)",
                    min_rating, max_rating, platform, page_num, page_found, results,
                )
                page_num += 1
                if page_num <= 50 and consecutive_empty < 2:
                    await asyncio.sleep(random.uniform(3, 6))

            _write_health(self.db_path, source, success=True, records_written=results["updated"])
            return results

        except Exception as exc:
            _write_health(self.db_path, source, success=False, error_text=str(exc))
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
        Navigate /cheapest-by-rating/ ONCE, switch platform via URL param, then use a
        single JS evaluate() call to extract all rating sections in one DOM pass.

        Returns {rating: snapshot_dict} for every rating that yielded at least one valid card.
        Returns empty dict if the page yields no sections (caller decides whether to fallback).
        """
        ratings_set = set(ratings or range(81, 94))
        plat_param = "pc" if platform == "pc" else "console"
        # Platform passed as URL param — avoids the Radix dropdown (which times out).
        url = f"https://www.fut.gg/cheapest-by-rating/?platform={plat_param}"
        page = await self._new_page()
        results: dict[int, dict[str, Any]] = {}
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            await self._navigate(page, url)
            # Wait for section headings to appear
            try:
                await page.wait_for_selector('h2, h3', timeout=30000)
            except Exception:
                pass
            # Then wait for at least one card anchor (any href containing /26-)
            try:
                await page.wait_for_selector('a[href*="/26-"]', timeout=60000)
            except Exception:
                pass

            # Single DOM pass via JS evaluate.
            # DOM structure on /cheapest-by-rating/:
            #   h2 → inside div.flex-between (heading row)
            #   h2's grandparent = section wrapper div
            #   all card anchors are inside the grandparent
            #   anchor.innerText = "PlayerName\nPriceStr\nPosition\nRating"
            # NOTE: the JS string is a raw Python string; \n inside is a literal backslash-n
            # that becomes the JS string escape sequence for newline.
            js = (
                "(() => {"
                "  var heads = document.querySelectorAll('h2, h3');"
                "  var sections = [];"
                "  for (var i = 0; i < heads.length; i++) {"
                "    var h = heads[i];"
                "    var m = h.textContent.match(/Cheapest (\\d+) Rated/);"
                "    if (!m) continue;"
                "    var rating = parseInt(m[1]);"
                "    var gp = h.parentElement && h.parentElement.parentElement;"
                "    if (!gp) continue;"
                "    var anchors = gp.querySelectorAll('a[href*=\"/26-\"]');"
                "    var cards = [];"
                "    for (var j = 0; j < anchors.length; j++) {"
                "      var a = anchors[j];"
                "      var parts = a.innerText.split('\\n').map(function(s){return s.trim();}).filter(Boolean);"
                "      if (parts.length < 2) continue;"
                "      cards.push({href: a.getAttribute('href') || '', parts: parts});"
                "    }"
                "    sections.push({rating: rating, cards: cards});"
                "  }"
                "  return sections;"
                "})()"
            )
            sections: list[dict] = await page.evaluate(js)

            if not sections:
                logger.warning(
                    "fetch_fodder_all_ratings: JS evaluate returned no sections for platform=%s",
                    platform,
                )
                return {}

            con = sqlite3.connect(self.db_path)
            with con:
                for section in sections:
                    card_rating: int = section["rating"]
                    if card_rating not in ratings_set:
                        continue

                    cards: list[dict[str, Any]] = []
                    for raw in section["cards"]:
                        # parts format: [name, price_str, position, rating_str]
                        parts = raw.get("parts", [])
                        if len(parts) < 2:
                            continue
                        player_name = parts[0]
                        price = _parse_price(parts[1])
                        position = parts[2] if len(parts) >= 3 else ""
                        card_version = ""  # not available on this page
                        if price is None or price < 200:
                            continue

                        href = raw.get("href", "")
                        card_key = _card_key_from_href(href) or ""

                        cards.append({
                            "card_key": card_key,
                            "player_name": player_name,
                            "rating": card_rating,
                            "position": position,
                            "club_name": "",
                            "nation_name": "",
                            "club_badge_url": "",
                            "nation_flag_url": "",
                            "card_version": card_version,
                            "bin_price": price,
                        })
                        if len(cards) >= 10:
                            break

                    if not cards:
                        logger.warning(
                            "fetch_fodder_all_ratings: no valid cards for rating=%d platform=%s",
                            card_rating, platform,
                        )
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
        2 page loads per sweep: fetch_fodder_all_ratings(pc) + fetch_fodder_all_ratings(console).
        Returns total snapshot rows inserted.
        """
        ratings = ratings or list(range(81, 94))
        platforms = platforms or ["pc", "console"]
        total = 0
        for plat in platforms:
            try:
                results = await self.fetch_fodder_all_ratings(plat, ratings)
                total += len(results)
                logger.info("fodder_sweep: platform=%s ratings_found=%d", plat, len(results))
            except Exception as exc:
                logger.error("fodder_sweep failed for platform=%s: %s", plat, exc)
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

            # Authoritative tradeability check: untradeable cards have no Prices
            # tab / Price Momentum section — their coin value is an SBC estimate.
            if not await _page_is_tradeable(page):
                logger.info(
                    "fetch_card_prices: %s (%s) has no Prices tab — marking untradeable, no snapshot",
                    player_name, version_name,
                )
                con = sqlite3.connect(self.db_path)
                with con:
                    con.execute("UPDATE cards SET tradeable=0 WHERE id=?", (card_id,))
                _write_health(self.db_path, self.SOURCE, success=True, records_written=0)
                return {"card_key": card_key, "platform": platform, "bin_price": None, "tradeable": 0}

            # Get main card price badge (first .fc-card-container .font-din)
            badge_el = page.locator(".fc-card-container .font-din").first
            await badge_el.wait_for(timeout=10000)
            badge_text = await badge_el.inner_text()
            _, _, bin_price = _parse_badge(badge_text)

            if bin_price is not None and not _is_real_bin_price(bin_price, version_name):
                logger.warning(
                    "fetch_card_prices: %s (%s) price %s fails FUT increment rules — snapshot skipped",
                    player_name, version_name, bin_price,
                )
                bin_price = None

            con = sqlite3.connect(self.db_path)
            with con:
                # Page has a Prices tab → card provably trades; restore the flag.
                con.execute("UPDATE cards SET tradeable=1 WHERE id=?", (card_id,))
                _insert_snapshot(con, card_id, platform, bin_price)

            result = {"card_key": card_key, "platform": platform, "bin_price": bin_price, "tradeable": 1}
            _write_health(self.db_path, self.SOURCE, success=True, records_written=1)
            return result

        except Exception as exc:
            _write_health(self.db_path, self.SOURCE, success=False, error_text=str(exc))
            raise
        finally:
            await page.close()


# ---------------------------------------------------------------------------
# Standalone sweep entry (opens its own browser; scheduler uses the method
# on its shared scraper instead)
# ---------------------------------------------------------------------------

async def fetch_all_cards_paginated(
    platform: str,
    min_rating: int,
    max_rating: int,
    db_path: str,
) -> dict[str, int]:
    """Module-level wrapper for manual/test runs of the paginated sweep."""
    async with FutGGScraper(db_path=db_path) as scraper:
        return await scraper.fetch_all_cards_paginated(platform, min_rating, max_rating)  # type: ignore[arg-type]


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
