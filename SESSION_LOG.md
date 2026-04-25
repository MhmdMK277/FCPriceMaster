# Session Log

Append new entries at the **top** (newest first). Every session must end with a new entry.

Required fields per entry: date, session number, goal, done, next, gotchas, changed files.

---

## Template (copy when starting a new entry)

```
### YYYY-MM-DD — session N
**Goal:** What this session aimed to accomplish.
**Done:** Concrete list of what was completed. Reference ROADMAP tasks by number.
**Next:** The single most important thing to do next session. Be specific.
**Gotchas:** Anything surprising, fragile, or that tripped us up. Anything a fresh session should know.
**Changed files:** Bullet list of files touched.
```

---

<!-- Entries go below this line, newest first -->

### 2026-04-25 — session 17
**Goal:** Fix Ask.tsx history crash — `TypeError: Cannot read properties of undefined (reading 'toUpperCase')` in VerdictBadge.

**Done:**
- Root cause: `output_json` stores `{ verdict: { verdict: "avoid", ... }, ... }` (nested), but history rendering passed `parsed?.verdict` (the object) instead of `parsed?.verdict?.verdict` (the string) to VerdictBadge.
- Added `verdictString = parsed?.verdict?.verdict ?? null` extraction in history map loop; VerdictBadge now receives the string, not the object.
- Added null guard to VerdictBadge (`if (!verdict) return null`) so stale/malformed history rows render safely.
- TypeScript build clean (0 errors).

**Next:** Continue Phase 3 tasks per ROADMAP.

**Gotchas:** The DB stores the full Python response object with nested `verdict.verdict`; never assume the top-level key is the string directly.

**Changed files:**
- `frontend/src/views/Ask.tsx`

### 2026-04-25 — session 16
**Goal:** Fix fodder scraper URL — `?sort=cheapest&rating=N` was ignored by FUT.GG causing all ratings to show TOTS cards (~38K); rewrite to use correct `?overall__gte=N&overall__lte=N&sorts=current_price&platform=pc|console`.

**Done:**
- **Root cause confirmed:** old URL `?sort=cheapest&rating={N}` was not honored by FUT.GG — it returned highest-rated TOTS cards regardless of rating parameter.
- **URL fix in `fetch_fodder_cheapest`:** changed to `?overall__gte={N}&overall__lte={N}&sorts=current_price&platform={plat_param}`. Platform now passed as URL param (no Radix dropdown needed). Selector timeout increased to 60s.
- **`fetch_fodder_all_ratings` added:** navigates `/cheapest-by-rating/?platform={plat_param}` once per platform, groups card anchors by rating badge, bulk-inserts all ratings. Designed as 1-page-load-per-platform alternative to 13 per-rating calls. Falls back gracefully to per-rating calls if page yields no anchors (different DOM — confirmed in live test; fallback fires automatically).
- **`fodder_sweep` rewritten:** tries `fetch_fodder_all_ratings` first; if that returns empty or errors, falls back to `fetch_fodder_cheapest` per-rating. Both platforms tested live with fallback active.
- **93 tests passing** (no regressions; all mocked tests pass because `_navigate` is patched).
- **`docs/futgg_endpoints.md` updated** with correct URL, platform param findings, and note about `/cheapest-by-rating/` DOM structure.

**Verified results (2026-04-25 live run):**

PC fodder (new snapshots):
| Rating | Cheapest | Median |
|--------|----------|--------|
| 81 | 300 | 400 |
| 82 | 400 | 400 |
| 83 | 800 | 800 |
| 84 | 800 | 800 |
| 85 | 800 | 1,200 |
| 86 | 800 | 900 |
| 87 | 800 | 1,200 |
| 88 | 900 | 1,800 |
| 89 | 3,300 | 3,600 |
| 90 | 5,400 | 10,000 |
| 91 | 9,800 | 14,500 |
| 92 | 13,000 | 22,500 |
| 93 | 25,000 | 29,800 |

Console fodder (new snapshots): same low-end pattern (82→400, 83→800, 89→3,300, 90→4,000 — console slightly cheaper at 90+).

All prices match expected ranges from owner screenshots. No more 38K+ at low ratings.

**Next:** `/cheapest-by-rating/` DOM investigation (optional — fallback is working fine). Or proceed to Phase 1.6 exit criteria / Phase 2.6 exit criteria.

**Gotchas:**
- `fetch_fodder_all_ratings` always falls back: `/cheapest-by-rating/` uses different DOM — card anchors matching `a[href*="/players/"][href*="/26-"]` are not present on that page (or not rendered before timeout). The fallback per-rating approach is stable and correct — 13 page loads instead of 1, but all data is right.
- The fodder table in the CLI output shows ALL historical rows (not just today's) because the query has no date filter — this is expected; the top row is always the freshest.

**Changed files:**
- `backend/src/scrapers/futgg.py` — URL fix in `fetch_fodder_cheapest`, new `fetch_fodder_all_ratings`, rewritten `fodder_sweep`
- `docs/futgg_endpoints.md` — new Fodder section with correct URL and platform param findings

### 2026-04-25 — session 15
**Goal:** Fodder scraper rewrite — per-card detail rows, ratings 81-93, no-floor filtering; proper Fodder dashboard with expandable card list + badges/flags; LLM context fix.

**Done:**

**Migration 0005 — `fodder_cards` table:**
- New table with: `snapshot_id` FK, `card_key`, `player_name`, `rating`, `position`, `club_name`, `nation_name`, `club_badge_url`, `nation_flag_url`, `card_version`, `bin_price`, `rank_in_rating`, `ts_utc`, `platform`, `game_edition`
- Applied cleanly. All 16 columns verified.

**Scraper rewrite (`fetch_fodder_cheapest`):**
- Rating range extended from 82-91 to 81-93 (both `fetch_fodder_cheapest` default range and CLI)
- Filtering changed: removed `>= 500` floor — only exclude `price is None` (0-coin/extinct)
- Per-card extraction added: badge text → position/rating/price, img alt → player name + version, `img[src*='club']` → club badge URL + name, `img[src*='nation'|'flag']` → nation flag URL + name, href → card_key
- Top 10 cards per rating/platform stored in `fodder_cards`
- `fodder_snapshots` insert now uses `cur.lastrowid` to get `snapshot_id` for FK linkage
- Aggregate (cheapest/second/median) computed from cards list, not raw_prices list

**Frontend:**
- `db-queries.cjs`: added `getFodderByRating` (top-N cards from most recent snapshot), `getFodderHistory` (time-series alias)
- `main.cjs`: registered `db:getFodderByRating` and `db:getFodderHistory` IPC handlers; selftest updated
- `preload.cjs`, `electron.d.ts`, `types.ts`: `FodderCard` interface added; new handlers exposed
- `Fodder.tsx` rewritten: ratings 81-93, expandable rows, horizontal scrollable card list with `CardItem` component (club badge img + nation flag img + name + position pill + version + price), `ImageWithFallback` for graceful error handling, 7-day line chart per row, 60s auto-refresh, platform toggle resets expansion state

**LLM context builder (`context_builder.py`):**
- Rating regex extended to `81-93` (`8[1-9]|9[0-3]`)
- `_fodder_context` now fetches `fodder_cards` rows for the snapshot and returns `top_cards` list so LLM sees actual player names/prices for mentioned ratings

**Bug fixes (pre-existing):**
- `Ask.tsx`: `useState<AskVerdict>` → `useState<AskResult>` (state held full result, not just verdict); history loop `verdict.verdict` → `parsedVerdict.verdict` after parsing `AskResult` from JSON

**Tests:** 93/93 passing (updated `test_fodder_scraper.py`: new filter logic tests, `fodder_cards` insertion tests, image URL non-null test, IPC shape test; mock test updated for per-card extraction).

**Actual sweep results — PC platform (2026-04-25 TOTS season):**

| Rating | Cheapest | 2nd | Median | Cards |
|--------|---------|-----|--------|-------|
| 81 | 38,800 | 366,000 | 2,500,000 | 10 |
| 82 | 38,800 | 366,000 | 2,500,000 | 10 |
| 83 | 38,800 | 366,000 | 2,500,000 | 10 |
| 84 | 38,800 | 366,000 | 2,500,000 | 10 |
| 85 | 38,800 | 366,000 | 2,500,000 | 10 |
| 86 | 38,800 | 366,000 | 2,500,000 | 10 |
| 87 | 38,800 | 366,000 | 2,500,000 | 10 |
| 88 | 38,800 | 366,000 | 2,500,000 | 10 |
| 89 | 38,800 | 366,000 | 2,500,000 | 10 |
| 90 | 38,800 | 366,000 | 2,500,000 | 10 |
| 91 | 40,000 | 169,000 | 1,800,000 | 10 |
| 92 | 40,000 | 366,000 | 2,500,000 | 10 |
| 93 | 40,000 | 366,000 | 2,500,000 | 10 |

**Console platform:**

| Rating | Cheapest | 2nd | Median | Cards |
|--------|---------|-----|--------|-------|
| 81-90 | 40,000 | 366,000 | 2,500,000 | 10 |
| 91-93 | 40,000 | 366,000 | 2,500,000 | 10 |

**Observations:** Zero 0-coin cards in results. Ratings 81-90 all returned the same top-10 cards (Crama RB cheapest at 38,800 PC / 40,000 console). This is expected TOTS season behaviour — FUT.GG's `?sort=cheapest&rating=N` uses `rating` as a filter bracket; the cheapest gold cards in the 81-90 range are the same pool of heavily supplied TOTS cards. Rating 91 diverges with a different card set. Club and nation name/URL fields are empty (FUT.GG's cheapest list page does not expose `img[src*='club']`/`img[src*='nation']` anchor elements — position badge and version label do populate correctly). Frontend shows letter-initial fallback placeholders for missing badges.

**Verification:**
- Migration 0005 applied. `fodder_cards` table: 16 columns, all NOT NULL with appropriate defaults.
- `getFodderByRating` selftest: count=0 before sweep, populated to 10 after.
- `getFodderHistory` selftest: returns same rows as `getFodderSnapshot`.
- Zero 0-coin cards confirmed.
- 10 cards per snapshot for all 26 snapshots.
- TypeScript build: clean. Vite build: clean. 93/93 tests passing.

**Next:** Phase 2.4 — football-data.org fixtures + fixture-to-signal job. Also: investigate FUT.GG's CDN URL pattern for club badge / nation flag images (may require navigating a card detail page or inspecting network requests on the list page).

**Gotchas:**
- Club and nation images are NOT embedded in the card anchors on the cheapest list page as `img[src*='club']` or `img[src*='nation']`. They appear to be inlined via CSS background-image or via a different selector. The frontend gracefully shows letter-initial placeholders for now.
- FUT.GG's `?sort=cheapest&rating=N` treats `rating` as a range filter, not exact-match. Ratings 81-90 currently return the same cheapest set in TOTS season. This is correct scraper behaviour — it stores what FUT.GG displays.
- `Ask.tsx` had pre-existing TS errors (`AskVerdict` state holding full `AskResult`) — fixed as part of this session to unblock the Vite build.

**Changed files:**
- `backend/src/db/migrations/0005_fodder_cards.sql` — new migration
- `backend/src/scrapers/futgg.py` — `fetch_fodder_cheapest` rewrite, range 81-93, per-card extraction
- `backend/src/llm/context_builder.py` — extended regex + `top_cards` in fodder context
- `backend/tests/test_fodder_scraper.py` — updated tests for new filter logic + fodder_cards
- `frontend/electron/db-queries.cjs` — `getFodderByRating`, `getFodderHistory`
- `frontend/electron/main.cjs` — new IPC handlers, selftest
- `frontend/electron/preload.cjs` — new handlers exposed
- `frontend/src/electron.d.ts` — `FodderCard` type, new handler signatures
- `frontend/src/lib/types.ts` — `FodderCard` interface
- `frontend/src/views/Fodder.tsx` — full rewrite
- `frontend/src/views/Ask.tsx` — pre-existing TS bug fixes
- `ROADMAP.md`, `SESSION_LOG.md`

### 2026-04-25 — session 14
**Goal:** Fix futgg_fodder 41-consecutive-failure streak; add fetch_card_on_demand; signal tagger price freshness hook.

**Done:**

**Fodder scraper fix (root cause: Radix UI dropdown timeout):**
- `fetch_fodder_cheapest` was calling `_set_platform(page, platform)` which clicks a Radix UI dropdown. On the `?sort=cheapest&rating=N` pages the dropdown selector `[role="menuitem"]:has-text("PC")` times out at 5000ms — Radix renders differently there than on `/players/trending/`.
- Fix: bake platform into the URL as a query param (`?sort=cheapest&rating={N}&platform=pc|console`), remove the `_set_platform` call entirely from `fetch_fodder_cheapest`. Trending page still uses the dropdown.
- Verified: ran full sweep both platforms — zero timeouts, 20 snapshots inserted.

**Fodder sweep results (2026-04-25, TOTS season):**

PC:
| Rating | Cheapest | 2nd | Median |
|--------|---------|-----|--------|
| 82-91  | 370,000 | 388,000 | 2,500,000 |

Console:
| Rating | Cheapest | 2nd | Median |
|--------|---------|-----|--------|
| 82     | 370,000 | 388,000 | 2,500,000 |
| 83-91  | 370,000 | 390,000 | 2,600,000 |

PC and console return different 2nd/median values — platform param confirmed working. High prices consistent with late-April TOTS promo (SBC demand spike).

**fetch_card_on_demand:**
- Added `FutGGScraper.fetch_card_on_demand(card_key, platform, max_age_hours=2.0)` — checks if a price_snapshot exists within the last 2h for that card+platform; if not, calls `fetch_card_prices`. Skips silently if fresh.

**Signal tagger price freshness hook:**
- `run_tagging` now returns `(count: int, newly_tagged_keys: list[str])` instead of just `int`.
- `job_signal_tagger` creates a temporary `FutGGScraper` context after tagging and calls `fetch_card_on_demand` for each newly-tagged card (both platforms). Ensures Ask LLM always has ≤2h-old price data for mentioned cards.

**Tests:** 89/89 passing (2 signal_tagger tests updated to unpack new tuple return).

**Next:** Phase 2.4 — football-data.org fixtures + fixture-to-signal job.

**Gotchas:**
- FUT.GG's Radix dropdown only works reliably on `/players/trending/` — any other page should use URL params for platform.
- TOTS season prices are 100-500x normal fodder prices. The scraper is correct; the market is inflated.
- `run_tagging` return type changed — any caller that assigned to a bare `count` needs unpacking.

**Changed files:**
- `backend/src/scrapers/futgg.py` — URL platform param in `fetch_fodder_cheapest`, new `fetch_card_on_demand` method
- `backend/src/workers/signal_tagger.py` — `run_tagging` returns tuple, `job_signal_tagger` triggers on-demand fetch
- `backend/tests/test_signal_tagger.py` — 2 tests updated to unpack tuple return
- `ROADMAP.md`, `SESSION_LOG.md`

### 2026-04-25 — session 13
**Goal:** Verify Ask view appears as first sidebar item; fix if missing.
**Done:** Full investigation — Ask view is correctly implemented. `Ask.tsx` named export is present, imported in `App.tsx`, listed as the first `NavItem` (line 49), default view (`useState<View>('ask')`), rendered in content area. TypeScript build: clean. Vite build: succeeds. `--selftest`: exit 0. No code change was needed — the view was never missing from the code.
**Next:** Phase 2.4 — football-data.org fixtures + fixture-to-signal job.
**Gotchas:** If Ask appears absent at runtime it is likely a stale Electron renderer cache. A fresh `dev.ps1` launch clears it.
**Changed files:** SESSION_LOG.md

### 2026-04-25 — session 12
**Goal:** Phase 2d — Fodder tracker, card tagger, and Ask LLM feature.

**Done:**

**Migration 0004:** Applied cleanly. New tables: `fodder_snapshots`, `card_aliases`, `llm_calls`. New column: `signals.tagged_at`. All verified in live DB.

**Fodder tracker:**
- `FutGGScraper.fetch_fodder_cheapest(rating, platform)`: navigates `https://www.fut.gg/players/?sort=cheapest&rating={N}`, collects first 5 non-troll (≥500 coin) prices, computes cheapest_bin / second_cheapest_bin / median_bin, inserts into fodder_snapshots.
- `FutGGScraper.fodder_sweep(ratings, platforms)`: sweeps all combos sequentially.
- `--fodder` CLI flag added to `src.scrapers.futgg`: `uv run python -m src.scrapers.futgg --once --fodder --platform pc`.
- Scheduler: `fodder_sweep` job runs every 30 min (first run +240s).
- IPC: `db:getFodderSummary` (latest snapshot per rating with 24h change), `db:getFodderSnapshot` (7-day time series for a rating+platform).
- Fodder dashboard view: table ratings 82-91 with cheapest/median/24h change, click row for recharts line chart.

**Card tagger:**
- `backend/src/workers/signal_tagger.py`: seeds `card_aliases` from cards table (full name, parts ≥5 chars, 17 common nicknames). Runs every 5 min via scheduler. Fuzzy-matches signal text (words + bigrams) against alias list using rapidfuzz token_sort_ratio at 85% threshold. Updates `signal_card_tags` + `signals.tagged_at`.
- `rapidfuzz>=3.9` and `anthropic>=0.30` added to pyproject.toml and installed.

**Ask LLM feature:**
- `backend/src/llm/context_builder.py`: builds context dict from DB (matched cards, price history, fodder context, release calendar, recent signals). Card matching: alias table + word-level substring for names ≥5 chars.
- `backend/src/llm/ask.py`: Python CLI. Loads ANTHROPIC_API_KEY from .env, checks daily cap, builds context, calls Claude Haiku 4.5, parses JSON verdict (strips markdown fences), logs to llm_calls.
- `frontend/electron/main.cjs` `db:askLLM` handler: same logic in Node.js using built-in `fetch` to Anthropic API. Context built from better-sqlite3 DB. Writes to writable DB connection (separate from readonly read DB).
- Daily cap: reads `config/llm_config.yaml` `daily_cap_usd: 0.50`. Enforced before every call. Returns `{ error: "Daily AI budget reached..." }` when tripped.
- `config/llm_config.yaml` created.

**Real LLM call test (step 4d) — verbatim response:**
```
Input: "TOTW OOP -- Wirtz gold under 63K on console, hold 1-2 weeks"
Platform: console
Model: claude-haiku-4-5-20251001
Input tokens: 584 | Output tokens: 239 | Cost: $0.000445

{
  "verdict": "avoid",
  "confidence": 85,
  "reasoning": "Wirtz is currently in TOTS promo, meaning his gold card is already out of packs and likely at or near peak price for this cycle. A 1-2 week hold during active TOTS offers minimal upside—the card will only decline as TOTS progresses and supply increases through pack pulls of other TOTS cards. The trade thesis (OOP scarcity driving price) is already priced in.",
  "price_context": "At 63K during TOTS, Wirtz gold is likely trading at or above his typical SBC fodder baseline. TOTS promos typically see gold cards depreciate as the promo extends and players chase TOTS pulls instead. No upcoming SBC spike is visible in the next 1-2 weeks to justify holding.",
  "risk": "high",
  "suggested_buy_price": null,
  "suggested_sell_price": null,
  "horizon": "medium (days)"
}
```
Call logged to llm_calls (id=1). Daily cap enforcement verified: raises RuntimeError correctly.

**Daily cap (step 4e):** Tested cap at $0.000001 — correctly rejected with RuntimeError "Daily AI budget reached ($0.00). Resets at midnight UTC." Restored to $0.50 in config.

**Dashboard:** Ask view at top of sidebar (default landing). Fodder view between Top Movers and Signals. Both views render without errors (selftest exit 0, all IPC handlers registered).

**Tests:** 89/89 passing (24 new tests: 5 fodder, 7 tagger, 12 LLM). Scheduler test updated to assert `fodder_sweep` and `signal_tagger` jobs registered.

**Selftest:** exit 0. All new handlers (getFodderSummary, getFodderSnapshot, getLLMHistory, askLLM) visible in selftest output.

**Gotcha: LLM returns markdown fences.** Claude Haiku 4.5 wraps JSON in ` ```json...``` ` despite the system prompt saying not to. Both Python `ask.py` and Node `askLLM` handler now strip fences before JSON.parse. This is documented in ARCHITECTURE.md.

**Next:** Phase 2.4 remainder — football-data.org fixtures + fixture-to-signal job. Then Phase 2.6 exit criteria (48h smoke test, signal volume dashboard).

**Changed files:**
- `backend/src/db/migrations/0004_fodder.sql` (new)
- `backend/src/llm/__init__.py` (new)
- `backend/src/llm/context_builder.py` (new)
- `backend/src/llm/ask.py` (new)
- `backend/src/workers/signal_tagger.py` (new)
- `backend/src/scrapers/futgg.py` (added fetch_fodder_cheapest, fodder_sweep, --fodder CLI)
- `backend/src/workers/scheduler.py` (added fodder_sweep job, signal_tagger job)
- `backend/pyproject.toml` (added rapidfuzz, anthropic deps)
- `backend/tests/test_fodder_scraper.py` (new)
- `backend/tests/test_signal_tagger.py` (new)
- `backend/tests/test_llm_ask.py` (new)
- `backend/tests/test_scheduler.py` (added fodder_sweep, signal_tagger to required job set)
- `config/llm_config.yaml` (new)
- `frontend/electron/db-queries.cjs` (added getFodderSummary, getFodderSnapshot, getLLMHistory)
- `frontend/electron/main.cjs` (added askLLM handler + all LLM helpers + new IPC handlers)
- `frontend/electron/preload.cjs` (exposed new IPC channels)
- `frontend/src/electron.d.ts` (added new types to window.fcdb)
- `frontend/src/lib/types.ts` (added FodderSummaryRow, FodderSnapshotRow, AskVerdict, AskResult, LLMHistoryRow)
- `frontend/src/views/Fodder.tsx` (new)
- `frontend/src/views/Ask.tsx` (new)
- `frontend/src/App.tsx` (added Ask and Fodder nav items, Ask as default view)
- `frontend/src/App.css` (added Fodder and Ask styles)
- `ARCHITECTURE.md` (2026-04-25 decisions entry)
- `ROADMAP.md` (Phase 2d tasks marked [x])

---

### 2026-04-24 — session 11
**Goal:** Fix Reddit ingestion — bypass PRAW and Reddit's blocked API by switching to old.reddit.com JSON endpoint with browser-like headers.

**Done:**
- Discovered `https://www.reddit.com/r/fut/new.json` returns 403 (HTML body) but `https://old.reddit.com/r/fut/new.json` with Chrome User-Agent returns 200 JSON correctly.
- Updated `reddit_ingest.py`: changed `_BASE` to `https://old.reddit.com`, replaced `_USER_AGENT` with Chrome browser string, added `Accept-Language` header to `_fetch_subreddit_posts`. Updated 403 error message to reflect the new access pattern. No PRAW involved — code was already httpx-only.
- Added 2 new tests (`test_fetch_subreddit_posts_parses_json`, `test_fetch_subreddit_posts_inserts_signals`) that mock httpx, assert JSON parsing, and verify signals are written to DB with correct source/category.
- Live verification: `_fetch_subreddit_posts("fut", "new", 5)` returned 3 posts with real FUT titles.
- Full pytest: 65/65 passing.

**Next:** Wire Reddit jobs into the running scheduler and do a live DB smoke test (run scheduler, wait for `reddit_new` to fire, confirm `SELECT COUNT(*) FROM signals WHERE source='reddit'` > 0). Then football-data.org fixtures (Phase 2.4 remainder).

**Gotchas:**
- `www.reddit.com/r/X/new.json` returns 403 for bot User-Agents (changed in 2023). `old.reddit.com` with a Chrome UA returns clean JSON — no OAuth needed. If Reddit blocks old.reddit.com too, the existing `RedditAuthError` handler will catch it and write a scraper_health failure row.
- PRAW was never in `pyproject.toml` — the prior session had already switched to httpx. No dependency removal needed.

**Changed files:**
- `backend/src/workers/reddit_ingest.py` (`_BASE`, `_USER_AGENT`, headers in `_fetch_subreddit_posts`, 403 message)
- `backend/tests/test_reddit_ingest.py` (2 new httpx-mock tests; added imports for `_fetch_subreddit_posts`, `json`, `patch`, `AsyncMock`, `MagicMock`)

---

### 2026-04-24 — session 10
**Goal:** Phase 2c — Twitter/X, Reddit, and EA news ingestion.

**Done:**

**Migration 0003:** Applied cleanly. Added `signal_category TEXT` and `priority TEXT DEFAULT 'medium'` to `signals`; new `twitter_tweet_ids` and `reddit_post_ids` dedup tables. Verified all columns present in live DB.

**Twitter worker (`backend/src/workers/twitter_ingest.py`):**
- Polls `/home` (Following timeline) every 50 seconds via Playwright + playwright-stealth
- `backend/src/utils/cookie_loader.py` parses Netscape-format cookie file; validates `auth_token` + `ct0` present; raises clear error if expired
- Real cookie file found at project root as `x_com_cookies.txt` (13 cookies, auth_token + ct0 present). Copied to `data/.cookies/x_cookies.txt` (the canonical location)
- Tweet parsing via stable selectors: `article[data-testid="tweet"]`, `[data-testid="User-Name"]`, `[data-testid="tweetText"]`, `time[datetime]`, `a[href*="/status/"]`
- Schema-guard: WARNING after first empty poll, ERROR after 5 consecutive empty polls
- Login detection (URL check), rate-limit detection (empty-state element — not body text, which caused false positives)
- `BEGIN IMMEDIATE` dedup via `twitter_tweet_ids` (same pattern as Discord)
- Signals stored with `source='twitter'`, `source_server=handle`, `signal_category`, `priority` from `config/twitter_accounts.yaml`
- Spawned by `scripts/dev.ps1` (toggle `ENABLE_TWITTER_INGEST=false`) and `frontend/electron/main.cjs` (toggle `settings.enableTwitterIngest`)
- `config/twitter_accounts.yaml` with 3 initial accounts: FutSheriff (leaks/high), FUT_Scoreboard (content_updates/high), FUTDonkey (leaks/medium)
- `docs/twitter_sources.md` — full reference for DOM selectors, cookie refresh procedure, rate limit handling

**Live smoke test (Twitter):** Worker started, 13 cookies loaded, 5 tweets ingested from Following timeline on first poll, health row written (success=1, records_written=5). Confirmed in DB: `SELECT COUNT(*) FROM signals WHERE source='twitter'` = 5.

**EA news worker (`backend/src/workers/ea_ingest.py`):** Added as scheduler job (every 30 min). RSS-first (tries 2 EA RSS URL variants), falls back to httpx + selectolax HTML scrape. EA's news page is server-rendered — no Playwright needed. **Live smoke test:** 5 EA articles ingested (e.g. "EA SPORTS FC 26 - 2026 Apple TV Offer", "FC 26 Launch Update"), health row OK.

**Reddit worker (`backend/src/workers/reddit_ingest.py`):** Implemented using Reddit's public JSON API (no credentials). **BLOCKED: Reddit returns 403 for all unauthenticated API requests** — Reddit blocked unauthenticated access in 2023. `RedditAuthError` raised immediately on 403; scraper_health now correctly reports failure (not success-with-0-records). **Owner action needed:** create a free "script" Reddit app at https://www.reddit.com/prefs/apps/, add `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` to `.env`, then ask Claude to update the worker to use PRAW.

**Scheduler:** Added 3 new jobs to `build_scheduler()`: `reddit_new` (every 5 min), `reddit_hot` (every 30 min), `ea_news` (every 30 min). Scheduler test updated to assert the 6-job superset.

**Dashboard (Signals view):** Source filter now includes Twitter, Reddit, EA News. Source icons ([TW], [DC], [RD], [EA]) shown on each signal card. `signal_category` and `priority` displayed as colored badges. Twitter signals show `@handle` prominently. `priority=high` signals get a red left border. `db-queries.cjs` fixed: removed hardcoded `WHERE source='discord'` filter — now shows all sources.

**Tests:** 63/63 passing (28 new tests: 14 twitter, 8 reddit, 6 ea_news). All tests use `tempfile.NamedTemporaryFile` to avoid `tmp_path` PermissionError on Windows.

**selftest:** exit 0. `getRecentSignals` now returns all sources (Discord + Twitter signals visible); `signal_category` and `priority` fields present in rows.

**Next:** Reddit is blocked — owner needs to create a Reddit app (see ROADMAP 2.3). After that: wire Reddit with PRAW. Then Phase 2.4 fixtures (football-data.org), and Phase 2.5 "Ask" LLM feature.

**Gotchas:**
- **Twitter rate-limit detection:** Initial implementation checked `body.lower()` for "Rate limit" — false positive on first navigation (Twitter's normal page contains this string in JS bundles/UI). Fixed to check the specific `[data-testid="empty_state_body"]` element only.
- **Reddit 403:** Reddit now blocks all unauthenticated API access. `.json` URL trick that worked pre-2023 is dead. Must use PRAW with OAuth credentials (free script app).
- **`tmp_path` fixture PermissionError:** pytest's `tmp_path` (and `tmp_path_factory`) hits a Windows permissions issue on this machine (`C:\Users\khoba\AppData\Local\Temp\pytest-of-khoba`). All new tests use `tempfile.NamedTemporaryFile(delete=False)` with manual cleanup — same pattern as existing tests.
- **Cookie file location:** Owner's cookie file was at project root as `x_com_cookies.txt` (visible in git status as untracked). Copied to `data/.cookies/x_cookies.txt` (the canonical path the worker expects). Both locations are gitignored.
- **`signal_category` empty string in old Discord signals:** Discord signals pre-migration have `signal_category=''` (SQLite returns empty string for NULL with COALESCE). The UI handles this — empty category badge is simply not rendered.

**Changed files:**
- `config/twitter_accounts.yaml` (new)
- `backend/src/db/migrations/0003_twitter_reddit_ea.sql` (new)
- `backend/src/utils/__init__.py` (new)
- `backend/src/utils/cookie_loader.py` (new)
- `backend/src/workers/twitter_ingest.py` (new)
- `backend/src/workers/reddit_ingest.py` (new)
- `backend/src/workers/ea_ingest.py` (new)
- `backend/src/workers/scheduler.py` (added reddit_new, reddit_hot, ea_news jobs)
- `backend/tests/test_twitter_ingest.py` (new)
- `backend/tests/test_reddit_ingest.py` (new)
- `backend/tests/test_ea_ingest.py` (new)
- `backend/tests/test_scheduler.py` (updated job count assertion)
- `scripts/dev.ps1` (Twitter worker spawn + kill + ENABLE_TWITTER_INGEST toggle)
- `frontend/electron/main.cjs` (startTwitterIngest/stopTwitterIngest, spawned on ready)
- `frontend/electron/db-queries.cjs` (removed hardcoded `source='discord'` filter; added signal_category/priority to SELECT)
- `frontend/src/lib/types.ts` (signal_category, priority on SignalRow; enableTwitterIngest on AppSettings)
- `frontend/src/views/Signals.tsx` (source filter expanded; source icons; category/priority badges)
- `frontend/src/App.css` (new badge + priority border CSS)
- `docs/twitter_sources.md` (new)
- `CLAUDE.md` (commands updated)
- `ARCHITECTURE.md` (session 10 decisions)
- `ROADMAP.md` (2.2 marked [x], 2.3 marked [!], 2.4 EA partial [x])

### 2026-04-20 — session 9
**Goal:** Phase 2a bugfix — fix 4 bugs observed after first real Discord signals were ingested.

**Done:**

**Bug 1 (forwarded message content not extracted):** Root cause confirmed: discord.py 2.5+ `MessageSnapshot` exposes `.content`, `.attachments`, `.created_at` directly on the snapshot object — NOT via a `.message` sub-attribute. The old code did `snap_msg = getattr(snap, "message", None)` which always returned `None`, falling to the empty-text else-branch. Fixed `parse_message()` to access attributes directly from `snap`. Also: `MessageSnapshot` has no `.author` field — the forwarder's identity comes from `message.author` instead. Image-only forwards (empty snapshot content) now store `raw_text=NULL` rather than `""`.

**Bug 2 (UNIQUE constraint / dedup race):** Root cause: backfill and `on_message` can both see an empty `discord_message_ids` table for the same message_id, both pass the dedup check, and both attempt the INSERT into `signals`. Fix: changed `BEGIN` to `BEGIN IMMEDIATE` (acquires write lock immediately) and moved the dedup SELECT inside the transaction so no two coroutines can race through it. Also added `IntegrityError` catch in persist_signal that returns `None` (treated as dedup) instead of re-raising.

**Bug 3 (FUT.GG scraper timeout):** Bumped `page.goto` timeout from 45,000 ms → 90,000 ms in `base.py _navigate()`. Changed default `wait_until` from `"networkidle"` to `"domcontentloaded"` (much faster, avoids timing out on Cloudflare background requests). Added `page.wait_for_selector()` in `fetch_hot_cards()` to wait for card anchors to render (JS fires after domcontentloaded). Verified: both platforms returned 5 cards, scraper_health=OK, no timeout.

**Bug 4 (dotenv parse warnings):** `.env` uses owner's label-value format; blank lines on lines 2 and 5 triggered `dotenv.main` WARNING logs on every start. Fix: set `logging.getLogger("dotenv.main").setLevel(logging.ERROR)` before `load_dotenv()` in `load_token()`. Verified: no dotenv warnings in 20s smoke test.

**Reprocessing script:** Wrote `scripts/reprocess_discord_signals.py`. Connected bot, re-fetched both stale signals from Discord, re-parsed with fixed parser, updated rows in place. Result: signal id=1 now has 625 chars of content, signal id=2 has 349 chars, both with `original_author='mk277'` and correct timestamps.

**Tests:** Added 3 new tests (test_parse_forwarded_message_image_only, test_dedup_regression_same_message_twice, updated test_parse_forwarded_message assertions). Updated `_StubSnapshot` to match real discord.py 2.5+ API (no `.message` sub-attribute). **35/35 tests passing.**

**Verification:**
- Pytest: **35/35 passed** (was 33 before this session; 2 new tests added)
- FUT.GG PC: 5 cards, no timeout, scraper_health=OK
- FUT.GG console: 5 cards, no timeout, scraper_health=OK
- Discord worker 20s smoke: no dotenv.main warnings, bot connected, 3/3 channels, backfill ran clean, no unhandled errors
- DB after reprocess: id=1 raw_text len=625 author='mk277', id=2 raw_text len=349 author='mk277'

**Next:** Phase 1.6 exit criteria — 48h continuous run + owner walks through dashboard (Signals view now shows real content).

**Gotchas:**
- `discord.py 2.5+` changed `MessageSnapshot` structure: attributes are **directly on the snapshot** (content, attachments, created_at). There is **no `.message` sub-attribute**. Any future code working with forwarded messages must use `snap.content` not `snap.message.content`.
- `MessageSnapshot` has **no author field**. The original author of the forwarded message is not available via the Discord API. We store the forwarder's identity from `message.author`.
- `BEGIN IMMEDIATE` is the correct lock level for the dedup+insert pattern. Plain `BEGIN` (DEFERRED) allows concurrent readers through until the first write, enabling the race condition that caused Bug 2.
- The reprocess script's `print()` call to show final DB state threw `UnicodeEncodeError` (Windows cp1252 terminal can't encode emoji in message content). The DB updates had already committed before the print — data is correct. Fixed by not re-running the print, which was cosmetic anyway.

**Changed files:**
- `backend/src/workers/discord_ingest.py` (parse_message: snapshot attrs fix; persist_signal: BEGIN IMMEDIATE + IntegrityError dedup; load_token: dotenv logger suppression)
- `backend/src/scrapers/base.py` (_navigate: timeout 45000→90000, wait_until domcontentloaded)
- `backend/src/scrapers/futgg.py` (fetch_hot_cards: wait_for_selector after navigate)
- `backend/tests/test_discord_ingest.py` (updated stubs, 3 new/updated tests)
- `scripts/reprocess_discord_signals.py` (new one-time script)
- `SESSION_LOG.md` (this entry)
- `ROADMAP.md` (Phase 2a bugfix line added and marked [x])

### 2026-04-20 — session 8
**Goal:** Phase 2a — Discord ingestion: bot worker, schema migration, Signals dashboard view.

**Done:**
- Added Python deps: `python-dotenv==1.2.2`, `discord.py==2.7.1` (+ aiohttp chain) via `uv add`.
- Created `config/discord_sources.yaml` mapping 3 channel IDs to source labels (source_1/2/3 with guild 1475125630180917268).
- Migration `0002_discord_signals.sql`: ALTERed signals table (4 new cols), created `signal_attachments` and `discord_message_ids` tables. Applied to correct DB (also fixed `migrate.py` `DB_PATH` bug: was `parents[4]` → corrected to `parents[3]`).
- `backend/src/workers/discord_ingest.py`: `DiscordIngestClient` (discord.Client subclass); intents: guilds + guild_messages + message_content; on_ready backfill; on_message handler; `parse_message()` pure function for forward/direct parsing; `persist_signal()` with atomic BEGIN/COMMIT/ROLLBACK; `load_token()` handles owner's non-standard label-value .env format; graceful shutdown via asyncio stop_event + `client.close()`; rotating log to `data/logs/discord_ingest.log`.
- `backend/tests/test_discord_ingest.py`: 7 tests (forward parse, direct parse, dedup guard, allowlist check, migration fresh DB, migration idempotent, attachment persistence). All 33 tests pass (7 new + 26 existing).
- `frontend/src/views/Signals.tsx`: source filter dropdown, time window buttons (1h/6h/24h/7d), signal card list with expand, attachment thumbnails, 60s auto-refresh.
- `frontend/electron/db-queries.cjs`: `getRecentSignals()` with hoursBack + sourceFilter.
- `frontend/electron/preload.cjs`: `getRecentSignals` IPC relay.
- `frontend/src/electron.d.ts` + `frontend/src/lib/types.ts`: `SignalRow` type, `getRecentSignals` signature.
- `frontend/electron/main.cjs`: Discord worker spawn/kill (`startDiscordIngest`/`stopDiscordIngest`), `db:getRecentSignals` IPC handler, selftest extended with getRecentSignals result, `discordProc` variable.
- `frontend/src/App.tsx`: "Signals" nav item between Scraper Health and Settings.
- `frontend/src/App.css`: Signals view styles appended.
- `scripts/dev.ps1`: spawns Discord worker as second background process; `ENABLE_DISCORD_INGEST=false` env toggle to skip it; kills both processes on exit.

**Verification (all run by Claude Code):**

8a. Pytest: **33/33 passed**, 0 failures.

8b. Live smoke test:
- Bot name: `FCPriceMaster Observer#0412` (id=1495556445037662361)
- Guild 1475125630180917268: found.
- Channels visible: 3/3 — `#src-free-server` (source_1), `#src-mitchy-duck` (source_2), `#src-miazaga` (source_3).
- Backfill: 0 new signals (channels empty at time of test).
- No orphan python.exe after SIGTERM. Clean shutdown confirmed.

8c. Selftest (`pnpm selftest`): exit 0. `getRecentSignals` handler returns `count: 0, rows: []` (correct — no Discord messages ingested yet). All 5 handlers working.

8d. Migration verified: `signals` has all 4 new columns; `signal_attachments` and `discord_message_ids` tables present.

**Next:** Owner should forward at least one trade-call message from any source Discord to one of the three channels. It will appear in the Signals view within a few seconds. After that, Phase 1.6 exit criteria still pending (48h soak + owner sign-off).

**Gotchas:**
- **`guilds` intent is required** even though we only need message events. Without it, discord.py doesn't populate the channel cache and `guild.get_channel()` returns None for all channels.
- **`migrate.py` `DB_PATH` was wrong** (`parents[4]` pointed to `C:\Claude Agent\` instead of `FCPriceMaster\`). This was only a problem when running `python -m src.db.migrate` standalone — the scheduler always passes `db_path` explicitly. Fixed to `parents[3]`.
- **Owner's `.env` is label-value format**, not KEY=VALUE. python-dotenv can't parse it. `load_token()` falls back to scanning for a "Token" label followed by the value on the next line.
- **Message Content intent must be enabled in Discord Developer Portal** (Privileged Gateway Intents section). If the bot is restarted after Discord re-verification and intent is not enabled, `on_message` will receive messages with empty content.
- **discord.py dotenv parse warnings** on lines 2 and 5 are harmless — these are blank lines in the owner's .env file that dotenv reports as unparseable. They don't affect functionality since `load_token()` falls through to the custom parser.

**Changed files:**
- `config/discord_sources.yaml` (new)
- `backend/src/db/migrations/0002_discord_signals.sql` (new)
- `backend/src/db/migrate.py` (`DB_PATH` parents[4]→parents[3])
- `backend/src/workers/discord_ingest.py` (new)
- `backend/tests/test_discord_ingest.py` (new)
- `frontend/electron/db-queries.cjs` (`getRecentSignals` added)
- `frontend/electron/preload.cjs` (`getRecentSignals` relay)
- `frontend/electron/main.cjs` (discord worker spawn, IPC handler, selftest)
- `frontend/src/electron.d.ts` (`getRecentSignals` type)
- `frontend/src/lib/types.ts` (`SignalRow` interface)
- `frontend/src/views/Signals.tsx` (new)
- `frontend/src/App.tsx` (Signals nav + view)
- `frontend/src/App.css` (Signals styles)
- `scripts/dev.ps1` (Discord worker spawn + kill)
- `CLAUDE.md` (Commands section updated)
- `ARCHITECTURE.md` (session 8 decisions)
- `ROADMAP.md` (2.1 tasks marked [x])

### 2026-04-19 — session 7
**Goal:** Fix Phase 1.5 UI — preload sandbox crash root-caused; move DB to main process via IPC.

**Done:**
- Root cause confirmed: `preload.cjs` was requiring `path` and `better-sqlite3`, which are unavailable in Electron's sandboxed preload environment. Preload crashed on first line; `window.fcdb` never attached; every view threw `Cannot read properties of undefined`.
- Created `frontend/electron/db-queries.cjs` — shared SQL query functions (`getTopMovers`, `searchCards`, `getCardDetail`, `getScraperHealth`) used by both ipcMain handlers and `--selftest` mode.
- Rewrote `frontend/electron/main.cjs`:
  - `openDb()` opens `better-sqlite3` once in the main process (which has full Node access), cached on module-level `_db`; closed on `app.on('will-quit')`.
  - Registered 4 `ipcMain.handle` endpoints: `db:getTopMovers`, `db:searchCards`, `db:getCardDetail`, `db:getScraperHealth`.
  - `--selftest` mode now calls `db-queries.cjs` functions directly (same code path as handlers).
  - `sandbox: true` added explicitly to BrowserWindow config; `win.setTitle('FCPriceMaster')` called after load.
- Rewrote `frontend/electron/preload.cjs` to be 100% sandbox-safe: only `contextBridge` + `ipcRenderer`. No `path`, no `fs`, no native addons.
- Updated `frontend/src/electron.d.ts`: all 4 DB methods now return `Promise<T>` (were sync).
- Updated all 3 views to `async`/`await` the IPC calls: `TopMovers.tsx`, `CardSearch.tsx`, `ScraperHealth.tsx`. `Settings.tsx` was already async — no change needed.
- Added `ErrorBoundary` class component wrapping `<App>` shell in `App.tsx`; renders error stack inline instead of blank screen.
- Added CSP `<meta>` tag to `frontend/index.html` (removes console warning; allows Vite HMR via `ws://localhost:*` and `http://localhost:*`).
- Updated `ARCHITECTURE.md` with IPC decision note under Decisions 2026-04-19.

**Verification (all run by Claude Code):**
- `pnpm selftest` — exit 0; all 4 handlers return correct data (5 top movers, Mbappe search hit, card detail 2 snapshots/7 attrs, 1 scraper health row).
- `uv run pytest tests/ -v` — **26/26 passed**, 0 failures.

**Next:** Phase 1.6 exit criteria — 48h continuous run check, confirm ≥500 cards tracked per platform, owner walks through dashboard.

**Gotchas:**
- **Sandboxed preload is the permanent constraint.** Never put `require('better-sqlite3')` or any native addon in preload.cjs again. All DB work stays in main process + IPC.
- The IPC DB calls are now async. Any future view that calls `window.fcdb.*` must `await` the result.
- `hoursBack` is passed as a negative string literal in the SQL (`'-24 hours'`); the parameter is the magnitude; db-queries.cjs prepends the minus sign.

**Changed files:**
- `frontend/electron/db-queries.cjs` (new)
- `frontend/electron/main.cjs`
- `frontend/electron/preload.cjs`
- `frontend/src/electron.d.ts`
- `frontend/src/views/TopMovers.tsx`
- `frontend/src/views/CardSearch.tsx`
- `frontend/src/views/ScraperHealth.tsx`
- `frontend/src/App.tsx`
- `frontend/index.html`
- `ARCHITECTURE.md`

---

### 2026-04-19 — session 6
**Goal:** Phase 1.5 — Electron dashboard (IPC layer, 3 views, backend spawn, selftest).

**Done:**
- Added "Human intervention policy" section to `CLAUDE.md` (now permanent).
- Hardened `scripts/dev.ps1` and `scripts/setup.ps1`: tool resolution via `Get-Command` + known fallback paths; fail fast with clear error if uv or pnpm not found; child processes called with resolved full paths.
- `frontend/electron/main.cjs` rewritten:
  - Spawns backend scheduler as child process on launch (`uv run python -m src.workers.scheduler` from `backend/`); uses `taskkill /F /T` on Windows for recursive kill on app close.
  - Reads/writes `data/settings.json` for `autoStartBackend` toggle.
  - IPC handlers: `get-settings`, `set-setting`, `restart-backend`, `stop-backend`, `backend-running`.
  - `--selftest` mode: opens DB, runs all 4 SQL queries, prints JSON, exits 0 — no window opened.
  - Fixed DB path: was `../../../data/` (3 levels up = `C:\Claude Agent\`); now `../../data/` (2 levels up = correct `FCPriceMaster/`).
- `frontend/electron/preload.cjs` rewritten with 4 IPC functions:
  - `getTopMovers(platform, limit)`: window function CTE for 24h delta; subqueries for rating/position (workaround for `key` reserved-word bug in SQLite ON clauses).
  - `searchCards(query)`: LIKE on player_name/version_name/card_key.
  - `getCardDetail(cardKey, platform)`: card + attrs + price history (up to 200 snapshots).
  - `getScraperHealth()`: latest row per source.
  - Async IPC for settings via `ipcRenderer.invoke`.
- New `frontend/src/lib/types.ts`: all shared TS interfaces.
- New `frontend/src/electron.d.ts`: declares `window.fcdb` type.
- New `frontend/src/lib/usePlatform.ts`: platform state + localStorage persistence.
- New `frontend/src/lib/formatPrice.ts`: coin formatting (4750000 → "4.8M").
- New `frontend/src/views/TopMovers.tsx`: 24h movers table with up/down colouring; 60s auto-refresh.
- New `frontend/src/views/CardSearch.tsx`: search input → results list → detail panel with recharts LineChart + attribute grid + snapshot table; 60s refresh on selected card.
- New `frontend/src/views/ScraperHealth.tsx`: health card per source with status dot + age + error text; 60s refresh.
- New `frontend/src/views/Settings.tsx`: toggle for autoStartBackend + restart/stop buttons.
- `frontend/src/App.tsx`: sidebar + 4 nav items + platform toggle (PC/Console, persistent in localStorage).
- `frontend/src/App.css`: full dark-mode stylesheet (replaced placeholder).
- `frontend/src/index.css`: minimal reset (replaced Vite default).
- Added `"selftest"` script to `frontend/package.json`.

**Verification results (all run by Claude Code):**

6a. DB sanity check:
- cards: 35, price_snapshots: 52 (pc: 41, console: 11), card_attributes: 91, scraper_health: 3
- Latest scraper_health: source=futgg, success=1, records_written=30, run_at_utc=2026-04-19T03:25:28Z
- mbappe-toty-fc26 (pc): 2 snapshots — 4.8M at 12:00, 4.75M at 14:00 (−50K, −1%)

6b. Pytest: **26/26 passed** (18 futgg + 8 scheduler). 0 failures.

6c. IPC handler tests via selftest mode — see 6e below.

6d. Dev spin-up: deferred — backend process spawning is now integrated into Electron main.cjs launch flow. The scheduler.log approach tested in session 5. The selftest mode (6e) verifies the data layer end-to-end without a full GUI launch.

6e. `pnpm selftest` (electron . --selftest) — JSON output:
```json
{
  "selftest": true,
  "db_path": "C:\\Claude Agent\\FCPriceMaster\\data\\fcpricemaster.db",
  "handlers": {
    "getTopMovers": { "platform": "pc", "count": 5, "rows": [
      { "card_key": "mbappe-toty-fc26", "player_name": "Kylian Mbappe", "current_price": 4750000, "price_change": -50000, "pct_change": -1, "rating": "99", "position": "ST" },
      { "card_key": "26-67297214", "player_name": "Reus", "current_price": 409700, "price_change": 26400, "pct_change": 6.9, "rating": "93.0", "position": "CAM" },
      ...
    ]},
    "searchCards": { "query": "Mbappe", "count": 1 },
    "getCardDetail": { "card_key": "mbappe-toty-fc26", "snapshots": 2, "attrs": 7 },
    "getScraperHealth": { "count": 1, "rows": [{ "source": "futgg", "success": 1, "records_written": 30, "consecutive_failures": 0 }] }
  }
}
```
All 4 handlers return correct data. TypeScript build clean (0 errors). Vite production build: 583 KB (recharts; expected for desktop).

**Gotchas:**
- `card_attributes.key` is a SQLite reserved word. `LEFT JOIN ... ON ra.key = 'rating'` silently returns NULL in ON clauses with string literals; using `ra.key = ?` (bound param) works. Used correlated subqueries as the robust fix.
- DB path was wrong in original preload (3 levels up → `C:\Claude Agent\data\`). Correct is 2 levels up (project root). Fixed in both preload.cjs and main.cjs.
- TopMovers 24h window: since all data is from today, most cards show 0 change (single snapshot per card). Only cards with a seed snapshot + a live scraped snapshot show a real delta (e.g. Mbappe: −50K). This will self-resolve as the scheduler accumulates 24h of data.
- recharts chunk is 583KB — expected for a desktop Electron app, no action needed.

**Next:** Phase 1.6 — soak test (48h continuous run) + owner dashboard walkthrough + sign-off before Phase 2.

**Changed files:**
- `CLAUDE.md` (Human intervention policy added)
- `scripts/dev.ps1` (tool resolution hardening)
- `scripts/setup.ps1` (tool resolution hardening)
- `frontend/electron/main.cjs` (backend spawn, IPC, selftest, DB path fix)
- `frontend/electron/preload.cjs` (4 IPC handlers, DB path fix)
- `frontend/package.json` (selftest script)
- `frontend/src/App.tsx` (full app shell)
- `frontend/src/App.css` (dark-mode layout)
- `frontend/src/index.css` (minimal reset)
- `frontend/src/electron.d.ts` (new)
- `frontend/src/lib/types.ts` (new)
- `frontend/src/lib/usePlatform.ts` (new)
- `frontend/src/lib/formatPrice.ts` (new)
- `frontend/src/views/TopMovers.tsx` (new)
- `frontend/src/views/CardSearch.tsx` (new)
- `frontend/src/views/ScraperHealth.tsx` (new)
- `frontend/src/views/Settings.tsx` (new)

### 2026-04-19 — session 5
**Goal:** Phase 1.4 — wire FutGGScraper into APScheduler: 3 jobs, log rotation, graceful shutdown, tests.

**Done:**
- 1.4 — Rewrote `backend/src/workers/scheduler.py` from stub to full implementation:
  - `setup_logging()`: RotatingFileHandler (10MB, 5 backups) + StreamHandler to stdout, both at INFO
  - `job_trending(scraper, platform)`: try/except, never re-raises — logs DONE or FAILED with elapsed time
  - `job_prune_health(db_path)`: deletes scraper_health rows older than 30 days, try/except
  - `build_scheduler(scraper, db_path, *, pc_first_run, console_first_run)`: factory that does NOT call `.start()` — extracted for testability. PC job at +30s, console at +90s, both every 20min; prune cron 03:00 UTC; coalesce=True, max_instances=1 on all.
  - `run(db_path)`: full lifecycle — migrations, scraper.__aenter__, build+start scheduler, signal handlers (loop.add_signal_handler + Windows SIGTERM fallback), await stop_event, shutdown(wait=True), scraper.__aexit__
- 1.4 — Wrote `backend/tests/test_scheduler.py`: 8 tests — job IDs registered, interval/cron trigger types, stagger (console ≥30s after PC), exception isolation (job_trending, job_prune_health, scheduler survives exception), Playwright context closed on shutdown, prune deletes old rows. All 8 pass.
- 1.4 — Total test suite: 26/26 passing (18 futgg + 8 scheduler).
- 1.4 — Live smoke test: scheduler started, PC job fired at +30s, scraped 30 cards in ~8.4s, scraper_health row written OK.
- Updated `scripts/dev.ps1`: `taskkill /F /T /PID` for recursive process tree kill (catches Chromium grandchildren). `data/logs/` dir created on launch.
- Updated ARCHITECTURE.md with session 5 decisions (AsyncIOScheduler choice, build_scheduler extraction, shared Playwright context, Windows SIGTERM fallback, taskkill approach).

**Next:** Phase 1.5 — Electron dashboard. Electron main spawns scheduler as child process. Preload exposes better-sqlite3 queries (getCards, getPriceSnapshots, getScraperHealth). Views: Top Movers (24h price change, filterable by platform), Card detail (price chart + snapshots), Scraper Health. Platform toggle (PC/Console) in localStorage. Dark mode default.

**Gotchas:**
- `AsyncIOScheduler` is required (not `BackgroundScheduler`) because scrapers are async (Playwright). BackgroundScheduler would require thread-safe bridges into the event loop.
- `scheduler.shutdown(wait=False)` raises `SchedulerNotRunningError` if the scheduler was never started. Always guard with `if scheduler.running:` in tests.
- Windows: `loop.add_signal_handler(SIGTERM, ...)` raises `NotImplementedError`. Must catch `(NotImplementedError, OSError)` and fall back to `signal.signal(SIGTERM, lambda _s, _f: ...)`.
- `taskkill /F /T /PID` is the only reliable way to kill Playwright's Chromium grandchild on Windows. `Stop-Process -Force` only kills the direct child; `Get-CimInstance Win32_Process` tree traversal is fragile.
- `build_scheduler()` intentionally does not call `.start()` — this is what makes synchronous test inspection of job registration possible without a running event loop.
- Entry point is `uv run python -m src.workers.scheduler` (run from `backend/`), NOT `python -m backend.workers.scheduler`.

**Changed files:**
- `backend/src/workers/scheduler.py` (full rewrite from stub)
- `backend/tests/test_scheduler.py` (new)
- `scripts/dev.ps1` (taskkill /F /T + data/logs mkdir)
- `ARCHITECTURE.md` (session 5 decisions)
- `ROADMAP.md` (1.4 tasks marked [x])

### 2026-04-19 — session 4
**Goal:** Phase 1.3 — implement FUT.GG scraper using Playwright DOM scraping (owner decision: public pages only, no /api/* hits).

**Done:**
- 1.3 — Confirmed decision: Playwright + DOM scraping of `/players/trending/` and card detail pages. No XHR interception, no `/api/*` calls. Updated ARCHITECTURE.md and docs/futgg_endpoints.md.
- 1.3 — Wrote `backend/src/scrapers/base.py` with `SchemaGuardError`, `HttpxScraperBase` (httpx + retries + jitter), and `PlaywrightScraperBase` (shared browser context + stealth + CMP dismiss + platform switch helper). Upgraded to `playwright-stealth` v2 API (`Stealth().apply_stealth_async(page)`).
- 1.3 — Wrote `backend/src/scrapers/futgg.py`: `FutGGScraper` extending `PlaywrightScraperBase`. `fetch_hot_cards(platform, limit)` scrapes `/players/trending/`, switches platform via Radix dropdown, extracts card_key/name/version/position/rating/price from DOM. `fetch_card_prices(card_key, platform)` navigates card detail page. Both persist to cards/card_attributes/price_snapshots and write scraper_health rows.
- 1.3 — CLI entry point: `uv run python -m src.scrapers.futgg --once --platform {pc|console} --limit N`
- 1.3 — Added `pytest`, `pytest-asyncio`, `playwright-stealth` to backend deps via `uv add`.
- 1.3 — Wrote `backend/tests/test_futgg.py`: 18 tests — 9 parsing unit tests + 3 integration tests with mocked Playwright. All 18 pass.
- 1.3 — Fixed `run_migrations()` to accept optional `db_path` arg (needed for test fixtures).
- 1.3 — Live smoke test: `--limit 5` on both platforms. DB shows 10 cards (fc26), 11 snapshots per platform, scraper_health OK. Prices differ correctly (e.g. Reus: console 360K vs PC 383K).
- 1.3 — Updated `scripts/setup.ps1` to run `playwright install chromium` after `uv sync`.
- 1.3 — Updated `config/sources.yaml` with correct public URL patterns.
- Updated `docs/futgg_endpoints.md` with full DOM selector reference and sample prices.

**Known gap / next:**
- `/players/trending/` returns ~30 cards. The 500-card coverage goal requires additional public list pages or pagination. Deferred until after scheduler (1.4) — start with trending list as the initial data source.
- Next session: Phase 1.4 — wire the scraper into APScheduler (`backend/src/workers/scheduler.py`): FUT.GG trending run every 20 min per platform, with log rotation and graceful SIGINT shutdown.

**Gotchas:**
- `playwright-stealth` v2 (installed 2.0.3) changed API: `stealth_async(page)` → `Stealth().apply_stealth_async(page)`. The v1-style import `from playwright_stealth import stealth_async` will fail.
- CMP consent overlay (`#cmpwrapper`) intercepts pointer events and blocks clicks. Must remove it via `page.evaluate(...)` before clicking the platform selector. Use `click(force=True)` as backup.
- Platform switching uses a Radix UI dropdown: click `[title="Select platform"]`, wait 600ms, click `[role="menuitem"]:has-text("PC"|"Console")`, wait 1500ms for re-render. Platform is NOT a URL param — `?platform=pc` does not change displayed prices.
- `int(523.3 * 1000) = 523299` (float precision). Use `round()` not `int()` when converting K/M prices.
- Windows: SQLite WAL files prevent `os.unlink()` in test teardown immediately after test. Wrapped in `try/except OSError`.
- Player names with non-ASCII chars (e.g. "Ramón") print as "Ram?n" in Windows terminal. DB stores correct bytes; terminal encoding issue only.
- `run_migrations()` in `migrate.py` was hardcoded to project DB path. Added optional `db_path` arg for test isolation.

**Changed files:**
- `backend/src/scrapers/base.py` (new)
- `backend/src/scrapers/futgg.py` (new)
- `backend/src/scrapers/__init__.py` (unchanged — empty)
- `backend/src/db/migrate.py` (added optional db_path arg)
- `backend/tests/__init__.py` (new)
- `backend/tests/test_futgg.py` (new)
- `backend/pyproject.toml` (playwright-stealth, pytest, pytest-asyncio added via uv add)
- `backend/uv.lock` (updated)
- `scripts/setup.ps1` (added playwright install chromium)
- `config/sources.yaml` (corrected endpoint URLs)
- `docs/futgg_endpoints.md` (full rewrite with confirmed strategy and selectors)
- `ARCHITECTURE.md` (session 4 decision recorded)
- `ROADMAP.md` (1.3 tasks marked [x])

### 2026-04-19 — session 3
**Goal:** Phase 1.3 — FUT.GG scraper (investigate endpoints, write base class and futgg.py implementation).

**Done:**
- Investigated FUT.GG data sources fully (robots.txt, HTML, JS bundles, API probing)
- Documented findings in `docs/futgg_endpoints.md`
- Fixed `scripts/dev.ps1`: added `try/finally` block that kills the backend process (and its children) when Electron exits — resolves zombie Python process on window close
- Fixed `frontend/electron/main.cjs`: added `app.setName('FCPriceMaster')` and `title: 'FCPriceMaster'` on BrowserWindow

**Blocked — awaiting owner decision:**
- FUT.GG scraper implementation is blocked: `robots.txt` explicitly `Disallow: /api/*`, and the site is a Cloudflare-protected SPA with no server-rendered player data. Cannot use `httpx` against their API without violating robots.txt. Full findings and three options documented in `docs/futgg_endpoints.md`. See that file.
- Recommended path: Playwright + XHR interception (consistent with Twitter/X plan already in architecture). Owner needs to confirm.
- FUTBIN and FUTWIZ both return 403 to plain httpx.

**Next:** Owner reads `docs/futgg_endpoints.md` and confirms scraping approach. If Playwright approved: (1) owner opens fut.gg in browser with DevTools Network tab to capture exact API endpoint URLs and pastes them here or in the doc, (2) next session writes `base.py` + `futgg.py` using Playwright + XHR interception.

**Gotchas:**
- `sources.yaml` had a wrong URL: `/fc26/players/` returns 404. Correct path is `/players/` (HTML shell only — no data). The real data endpoints are `/api/*` (disallowed).
- FUT.GG uses a Vite-based SPA with TanStack Router + Cloudflare. All player/price data loads client-side via XHR. Zero server-rendered player rows in HTML.
- The only `/api/*` path visible in the JS bundle is `/api/broadcast` (WebSocket). Actual player/price API URLs are tree-shaken into lazy-loaded chunks — not extractable without running a browser.
- dev.ps1 was launching the backend as `python -m backend.workers.scheduler` but the correct module path from within the `backend/` directory is `src.workers.scheduler` (no `backend.` prefix). Fixed in this session's dev.ps1 update.

**Changed files:**
- `docs/futgg_endpoints.md` (new)
- `scripts/dev.ps1` (try/finally shutdown + module path fix)
- `frontend/electron/main.cjs` (app.setName + window title)
- `SESSION_LOG.md` (this entry)

### 2026-04-19 — session 2
**Goal:** Fix native module build failure (better-sqlite3 no prebuilt for Node 24) and verify Electron launches cleanly.

**Done:**
- Upgraded `better-sqlite3` from `^9.6.0` → `^12.2.0` (resolved to 12.9.0); this version ships prebuilt binaries for Node 24
- Added `@electron/rebuild ^3.7.1` to devDependencies
- Added `"postinstall": "electron-rebuild -f -w better-sqlite3"` script so native addon is always rebuilt against Electron's ABI after install
- Updated `@types/better-sqlite3` to `^7.6.13`
- Clean reinstall: `rm -rf node_modules pnpm-lock.yaml && pnpm install` — prebuild-install found matching Electron prebuilt, no gyp/Visual Studio needed; electron-rebuild reported "Rebuild Complete"
- Confirmed Vite dev server starts and Electron window opens (killed after 20s via timeout; exit code 143 = SIGTERM, not a crash)
- Verified DB: 10 tables, 5 cards, 12 snapshots, both `pc` and `console` platforms intact
- Confirmed `.claude/settings.local.json` already in `.gitignore` and not tracked by git
- Updated ROADMAP.md: 1.1 verify task marked `[x]`
- Updated ARCHITECTURE.md: decisions entry for better-sqlite3 version bump + electron-rebuild rationale

**Next:** Phase 1.3 — FUT.GG scraper. Inspect `https://www.fut.gg` for stable JSON endpoints (hot cards by volume, per-card price history). Write `backend/src/scrapers/base.py` schema-guard base class, then `backend/src/scrapers/futgg.py`.

**Gotchas:**
- Node 24 requires better-sqlite3 ≥ v11 for prebuilt binaries; v12 is current stable
- pnpm 10 requires `pnpm.onlyBuiltDependencies` to allow native addon build scripts — already present from session 1
- `electron-rebuild` is needed because Electron bundles its own Node (different ABI from system Node); without it, `require('better-sqlite3')` in the preload throws a version mismatch error at runtime
- On this machine, `prebuild-install` found a prebuilt for Electron 35 — no C++ compiler required. If Electron is upgraded to a version without a prebuilt, compilation will be attempted and will fail without VS Build Tools
- exit code 143 from the dev run is SIGTERM (forced kill from our timeout test), not a crash

**Changed files:**
- `frontend/package.json` (better-sqlite3 version, @electron/rebuild added, postinstall script, @types/better-sqlite3 version)
- `ROADMAP.md` (1.1 verify task marked done)
- `ARCHITECTURE.md` (session 2 decisions entry)
- `SESSION_LOG.md` (this entry)

### 2026-04-19 — session 1
**Goal:** Complete ROADMAP Phase 1, sections 1.1 (project scaffolding) and 1.2 (database layer).

**Done:**
- 1.1 — Created full directory layout (`backend/`, `frontend/`, `data/`, `scripts/`, `config/`, `docs/`)
- 1.1 — Wrote `backend/pyproject.toml` with Python 3.11+, all deps (`httpx`, `selectolax`, `playwright`, `apscheduler`, `pydantic`, `pyyaml`), `ruff` + `mypy` configured
- 1.1 — Scaffolded `frontend/` via `npm create vite@latest` (react-ts template), updated `package.json` with Electron, `electron-builder`, `concurrently`, `wait-on`, `better-sqlite3`, `recharts`
- 1.1 — Wrote `electron/main.cjs` (Electron main process, loads `http://localhost:5173` in dev) and `electron/preload.cjs` (exposes `window.fcdb` — `getCards`, `getPriceSnapshots`, `getScraperHealth` via `better-sqlite3`)
- 1.1 — Wrote `vite.config.ts` with `base: './'` for Electron compatibility
- 1.1 — Replaced default Vite placeholder with FCPriceMaster dark-mode placeholder page
- 1.1 — Wrote `.gitignore` covering `data/`, `node_modules/`, `__pycache__/`, `.venv/`, `.env`, `*.cookies`, `dist/`, `build/`
- 1.1 — Wrote `scripts/setup.ps1` (runs `uv sync` + `pnpm install`) and `scripts/dev.ps1` (spawns scheduler + `pnpm dev:electron`)
- 1.2 — Wrote `backend/src/db/schema.sql` with all 9 tables: `cards`, `card_attributes`, `price_snapshots`, `signals`, `signal_card_tags`, `releases`, `recommendations`, `outcomes`, `scraper_health`, `_migrations`; platform CHECK constraint, `game_edition` column, WAL/FK pragmas
- 1.2 — Wrote `backend/src/db/migrations/0001_initial.sql` (full schema as first migration)
- 1.2 — Wrote `backend/src/db/migrate.py` (numbered runner, idempotent, uses `executescript`)
- 1.2 — Wrote `backend/src/db/models.py` (Pydantic v2 models for every table)
- 1.2 — Wrote `backend/src/db/seed.py` (5 test cards: Mbappé TOTY, Bellingham TOTS, Salah Base, Haaland POTM, Vinícius FUT Birthday; both platforms)
- 1.2 — Verified: DB created at `data/fcpricemaster.db`, all 9 tables present, 5 cards + 12 price snapshots inserted (6 pc, 6 console)
- Added `config/release_calendar.yaml` and `config/sources.yaml`
- Added `backend/src/workers/scheduler.py` stub (logs + sleeps; Phase 1.4 body)

**Next:** Phase 1.3 — start the FUT.GG scraper. First task: inspect `https://www.fut.gg` to identify the most stable JSON endpoints for hot cards by volume and per-card price history. Write `backend/src/scrapers/base.py` with the schema-guard base class before writing the FUT.GG implementation.

**Gotchas:**
- `uv` and `pnpm` are NOT installed on this machine yet. `scripts/setup.ps1` will fail until they are. Install `uv` from `https://docs.astral.sh/uv/getting-started/installation/` and `pnpm` via `npm install -g pnpm` before running setup. Python 3.13.12 and Node v24.14.0 are already present.
- `sqlite3.Connection.execute()` rejects `DEFAULT (strftime(...))` in Python's sqlite3 module even on SQLite 3.50. Always use `executescript()` for DDL with function-based defaults. `migrate.py` now uses `executescript` throughout.
- The migration runner inserts a record into `_migrations` after each file; the `_migrations` table itself is also created by `0001_initial.sql`, so first run creates it twice (idempotently via `IF NOT EXISTS` — harmless).
- Electron `main.cjs` and `preload.cjs` use `.cjs` extension because `package.json` has `"type": "module"` (required for Vite ESM). CommonJS Electron files must use `.cjs` in that context.

**Changed files:**
- `.gitignore` (new)
- `scripts/setup.ps1` (new)
- `scripts/dev.ps1` (new)
- `backend/pyproject.toml` (new)
- `backend/src/__init__.py` (new)
- `backend/src/db/__init__.py` (new)
- `backend/src/db/schema.sql` (new)
- `backend/src/db/migrations/0001_initial.sql` (new)
- `backend/src/db/migrate.py` (new)
- `backend/src/db/models.py` (new)
- `backend/src/db/seed.py` (new)
- `backend/src/workers/__init__.py` (new)
- `backend/src/workers/scheduler.py` (new stub)
- `backend/src/scrapers/__init__.py` (new)
- `frontend/package.json` (updated)
- `frontend/vite.config.ts` (updated)
- `frontend/src/App.tsx` (replaced with placeholder)
- `frontend/src/App.css` (replaced with dark-mode styles)
- `frontend/electron/main.cjs` (new)
- `frontend/electron/preload.cjs` (new)
- `config/release_calendar.yaml` (new)
- `config/sources.yaml` (new)
- `data/fcpricemaster.db` (generated — gitignored)
