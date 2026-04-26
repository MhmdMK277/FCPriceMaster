# Architecture

## Goal
A local tool that watches the EA FC Ultimate Team market, ingests signals (price history, leaks, Discord/Twitter chatter, fixtures, EA news), and surfaces trade calls with reasoning. Runs unattended on the owner's secondary PC (i7-9700K / RTX 2060 Super / 32GB), viewable from the main PC over LAN when needed.

---

## Core shape: signal pipeline + LLM reasoning, NOT a trained model

Supervised training would require months of labeled outcomes we don't have yet. Instead:

1. **Ingest** signals into SQLite continuously
2. **Compute** features on demand (momentum, volatility, signal counts, proximity to highs/lows)
3. **Retrieve** relevant context per card (recent price points, recent signals, release calendar proximity, relevant fixtures)
4. **Reason** via LLM with a structured prompt; get a structured call back (action, confidence, horizon, reasoning, target price)
5. **Log** the call with a timestamp
6. **Evaluate** 24–48h later: did price move as predicted? Log the outcome.
7. **Later (Phase 4+):** once months of outcomes exist, train a lightweight classifier (XGBoost or similar) on `features → direction` as a second opinion. Never replaces the LLM, augments it.

This shape is why we don't need years of training data to start. The LLM provides reasoning today; the feedback loop earns us the data for a supervised model later.

---

## Why these specific choices

### SQLite over Postgres
Single file, zero admin, perfect for local. WAL mode allows the scraper to write while Electron reads concurrently without locking.

### Electron DB access uses IPC (main process owns DB, preload relays via ipcRenderer)
**Decision 2026-04-19:** Electron's sandboxed preload cannot load native addons (`better-sqlite3`) or Node built-ins (`path`, `fs`). Attempting to do so causes "Unable to load preload script" and `window.fcdb` never attaches. Fix: DB is opened once in the main process (`openDb()`), SQL query functions live in `electron/db-queries.cjs` (shared by ipcMain handlers and `--selftest`), and preload.cjs exposes only `contextBridge` + `ipcRenderer` — no Node APIs. Renderer calls are all async (`ipcRenderer.invoke`). `sandbox: true` is now explicit in BrowserWindow config.

When UI needs to trigger an action (e.g. "force refresh this card," "dismiss this recommendation"), a small localhost FastAPI is added in a later phase — not before.

### Python backend as a single long-lived process
APScheduler inside one process running all scrapers on their own cadences. Graceful shutdown on SIGINT. Electron main process spawns it as a child on startup (with a user-togglable off switch).

### Dynamic card coverage, volume-tiered
- Early FIFA cycle: hot list ≈ 500 cards, cold list ≈ 1500
- Mid/late cycle (promos, TOTS): hot list scales to 750–1000
- Cards promote into hot list when they appear in any fresh signal (leak, SBC, fixture), demote after N days of no signal and flat price
- Hot list polled every ~20 min, cold list every ~2h

### Scraper resilience pattern (applies to every source)
Every scraper defines an expected response schema (minimal — the fields we actually read). On fetch:
1. Fetch with retries, respect rate limits, randomized UA
2. Validate schema
3. On mismatch → raise, write failure row to `scraper_health` with the diff, UI shows red badge
4. On success → write data + success row to `scraper_health`

Never return partially-parsed or silently stale data.

### Twitter/X via Playwright with a logged-in throwaway session
Free API is unusable for this use case. Accepted cost: fragility (DOM changes) handled by the schema-guard pattern above. ToS-adjacent but local personal use only — not redistributed, not monetized.

---

## Data model (ground truth in `backend/db/schema.sql`)

| Table | Purpose |
|---|---|
| `cards` | Master record per unique card (player + version). Stable `card_key` across FIFA editions where possible. |
| `card_attributes` | Tag-based key/value attributes: rating, position, league, nation, club, playstyles, playstyle_plus. Schema-free for cross-FIFA portability. |
| `price_snapshots` | `(card_id, platform, ts_utc, bin_price, volume_proxy)`. Time series, heavily indexed on `(card_id, platform, ts_utc)`. |
| `signals` | `(source, source_id, ts_utc, raw_text, signal_type, tagged_cards)`. Everything from Discord, Twitter, Reddit, EA news, fixtures. `tagged_cards` is a join table. |
| `releases` | Known and expected promos/SBCs. Partially seeded from `config/release_calendar.yaml`, augmented by leak signals. |
| `recommendations` | `(card_id, platform, ts_utc, call, confidence, horizon_hours, target_price, reasoning)`. LLM output. |
| `outcomes` | `(recommendation_id, evaluated_at_utc, price_then, price_now, verdict, notes)`. Fed back by an evaluator job. |
| `scraper_health` | Per source: last success, last failure, consecutive failures, last error text. |

### Platforms
Two distinct markets tracked separately: `pc` and `console`. All price queries MUST filter by platform. The schema enforces this with a NOT NULL `platform` column on `price_snapshots` and a CHECK constraint (`pc` or `console`).

---

## Release calendar
`config/release_calendar.yaml` holds rough annual date windows for known promos:
- Winter Wildcards — early December
- TOTY — early January
- Future Stars — mid-January
- FUT Birthday — March
- TOTS — April through June
- FC27 launch — late September

Every LLM prompt injects "Today is X; T-Y days from expected promo Z." Surprise promos come from leak signals (Phase 2).

Updating for FC27: add new dated windows to the YAML. No code change.

---

## Cross-FIFA transition
When FC27 launches:
- `card_attributes` schema unchanged (playstyles are tagged rows, not columns)
- Old FC26 data kept in place for historical context and model feedback
- `config/sources.yaml` URLs updated if FUT.GG etc. change routes
- `game_edition` column on `cards` and `price_snapshots` (added in Phase 1 schema) distinguishes editions
- LLM prompt template references current edition from config

No rewrite. Config edit + schema-guard alerts handle the transition.

---

## Decisions log

### 2026-04-18 — initial architecture
- Chose SQLite over Postgres for zero-admin local operation.
- Chose Electron + direct SQLite reads over a full API layer for Phase 1.
- Chose LLM reasoning over supervised ML for cold start; feedback loop to enable supervised later.
- Chose Playwright + logged-in session for Twitter (vs paid API $200/mo or skipping — both worse).
- Card coverage is dynamic/volume-tiered, not a fixed top-N.

(Add future decisions here with date.)

### 2026-04-19 — session 2 dependency fix

**Upgraded better-sqlite3 from ^9.6.0 to ^12.2.0 (installed 12.9.0).**
Node 24's ABI is not covered by better-sqlite3 v9 prebuilt binaries. v11+ added Node 24 prebuild coverage; v12 is the current stable series. Kept native better-sqlite3 (not WASM alternative) because prebuilts downloaded cleanly without requiring Visual Studio Build Tools.

**Added @electron/rebuild + postinstall script.**
better-sqlite3 is a native addon; it must be compiled (or a prebuilt downloaded) against Electron's bundled Node ABI, not system Node. `electron-rebuild -f -w better-sqlite3` runs automatically after `pnpm install` via the `postinstall` hook. On this machine, `prebuild-install` found a matching Electron prebuilt and skipped compilation entirely — no build toolchain needed.

### 2026-04-19 — session 3 — FUT.GG scraper approach (PENDING DECISION)

**FUT.GG is a Cloudflare-protected SPA; their API is robots.txt-disallowed.**
`robots.txt` has `Disallow: /api/*`. The site is a Vite SPA — all player/price data loads client-side via XHR to `/api/*`. Plain `httpx` cannot reach data (Cloudflare blocks it). The `/fc26/players/` URL in sources.yaml returns 404; correct HTML shell is at `/players/` but contains no player data.

**Decision (session 4): Playwright + DOM scraping of public FUT.GG pages.**
We navigate public URLs (`/players/trending/`, card detail pages) as a real browser user,
let JS render, and read the displayed price from the DOM. We do NOT intercept `/api/*` XHRs
and do NOT call any `/api/*` endpoint directly. The browser makes internal XHR calls; we
only read what is rendered on screen. This is the interpretation that respects `robots.txt`
while still getting rendered market price data.

Rejected: XHR interception (still hits `/api/*` directly), cloudscraper/cf-clearance/FlareSolverr
(adversarial, fragile, explicitly excluded). EA FC web app API noted as future Phase 2+ option
if FUT.GG changes their public pages.

Platform switching uses the Radix UI `[title="Select platform"]` dropdown (not a URL param).
`playwright-stealth` v2 applied via `Stealth().apply_stealth_async(page)`.
Full DOM selector details in `docs/futgg_endpoints.md`.

### 2026-04-19 — session 1 scaffolding decisions

**Electron main/preload use `.cjs` extension.**
`package.json` has `"type": "module"` (required for Vite ESM). Electron's `main` field must point to a CommonJS file; using `.cjs` extension lets Node treat it as CJS without requiring a separate `package.json` in the `electron/` folder.

**`migrate.py` uses `executescript()` throughout.**
Python's `sqlite3.Connection.execute()` rejects `DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))` even on SQLite 3.50 — the Python binding applies extra strictness on default values. `executescript()` passes the SQL directly to SQLite and works correctly. All DDL migrations must use `executescript`.

**`signal_card_tags` added as explicit join table.**
ARCHITECTURE.md described `tagged_cards` as a field on `signals`; implemented as a proper join table `signal_card_tags (signal_id, card_id)` for referential integrity and query efficiency.

**`_migrations` table lives in the same DB file.**
Migration state tracked in `_migrations` inside `data/fcpricemaster.db`. Kept alongside data (not a separate file) because the DB is single-file and gitignored; the table is idempotently created by both `0001_initial.sql` and `migrate.py`'s bootstrap step.

### 2026-04-19 — session 6 — Electron dashboard decisions

**`card_attributes.key` is a SQLite reserved word — use correlated subqueries.**
`LEFT JOIN card_attributes ra ON c.id = ra.card_id AND ra.key = 'rating'` silently returns NULL because `key` is treated as a keyword in the ON clause with a string literal. Workaround: correlated subquery `(SELECT value FROM card_attributes ca WHERE ca.card_id=c.id AND ca.key=? LIMIT 1)` with a bound parameter. Do NOT use `LEFT JOIN` with literal key comparisons anywhere in this codebase.

**DB path from Electron: 2 levels up from `frontend/electron/`.**
`path.join(__dirname, '..', '..', 'data', 'fcpricemaster.db')` resolves to `FCPriceMaster/data/`. Three levels up overshoots to `C:\Claude Agent\`. Both main.cjs and preload.cjs must use 2 levels.

**IPC architecture for Phase 1.5: direct better-sqlite3 in preload, ipcRenderer.invoke for settings.**
DB reads are synchronous (better-sqlite3) and exposed directly via contextBridge. Settings (which require main-process file I/O) go through ipcRenderer.invoke. This keeps the hot path (UI rendering) synchronous while keeping main-process state writes in the main process.

**`--selftest` flag on Electron main.**
`electron . --selftest` opens the DB in main process, runs all 4 IPC queries, prints JSON, exits 0. Used as a headless smoke test in CI/verification. Added as `pnpm selftest` script.

### 2026-04-20 — session 8 — Discord ingestion (Phase 2a)

**Discord ingestion uses bot-on-owner's-server + forward pattern. Bot never joins external servers.**
Owner forwards messages from external trading Discords into three channels on their own server
(category 1495557586869813369). Bot reads only those three channels and ignores everything else,
including any other server it may be accidentally added to.

**Message forwards parsed via discord.py `message_snapshots` API.**
`message.message_snapshots[0].message` contains original content, timestamp, author, attachments.
Non-forward (owner-typed) messages are logged as `signal_type='direct'` with `source_server='owner_direct'`.
All parsing is in the pure function `parse_message()` — testable without a Discord connection.

**Images stored as URLs only in 2a; vision-based card extraction deferred to Phase 2d LLM integration.**
`signal_attachments` table stores URL, content_type, width, height. No image download/processing yet.

**Dedup via `discord_message_ids` table, keyed on Discord's message ID.**
Discord message IDs are globally unique. The table provides an O(1) dedup check before any INSERT.
`signals.source_id` also stores the message ID — the UNIQUE(source, source_id) constraint is a
secondary guard, but the explicit dedup table is the primary check.

**Discord worker is a separate long-running process (not in APScheduler).**
The bot maintains a persistent WebSocket connection to Discord. It cannot be a scheduled job.
Electron main.cjs and dev.ps1 both spawn/kill it alongside the scheduler. Toggle via
`ENABLE_DISCORD_INGEST` env var (default: true) or `settings.enableDiscordIngest` in settings.json.

**`guilds` intent is required alongside `guild_messages` + `message_content`.**
Without `guilds` intent, discord.py does not populate the guild/channel cache, so
`guild.get_channel()` returns None even for visible channels. `guilds` is non-privileged.
`message_content` IS privileged — must be enabled in Discord Developer Portal.

**`migrate.py` default `DB_PATH` was `parents[4]` (wrong — pointed to `C:\Claude Agent\`).**
Fixed to `parents[3]` (correct — points to `FCPriceMaster/`). Previously only the scheduler
(which always passes `db_path` explicitly) used the correct path; standalone migration runs
would silently create a phantom DB in the wrong location.

**Owner's `.env` file uses label-value format, not standard KEY=VALUE dotenv format.**
`load_token()` first tries standard dotenv parsing, then falls back to scanning the file for a
line containing exactly "Token" followed by the token value on the next line.

### 2026-04-26 — session 19 — Fodder scraper rewrite: JS evaluate + DOM section traversal

**`/cheapest-by-rating/` DOM is structurally different from the player listing pages.**
The `h2` heading ("Cheapest 81 Rated Players") is inside a `div.flex-between` which is itself inside a section wrapper div. Each card anchor (`a[href*="/26-"]`) lives in a separate child div of the section wrapper — not inside the heading's direct sibling. There is no `.font-din` badge element on this page. The anchor's `innerText` delivers the card data as a newline-delimited string: `name\nprice_str\nposition\nrating`.

**`page.evaluate()` with a JS string concatenated from parts avoids `\n` escape issues.**
Python raw strings (`r"""..."""`) and multiline strings both cause `\n` inside JS string literals to be interpreted as Python newlines, producing JS `SyntaxError`. The workaround: build the JS string by concatenating Python string literals. The `'\\n'` in `a.innerText.split('\\n')` becomes the two-char sequence `\n` in the final JS string, which JS interprets as the newline split character.

**FUT price increment ladder (observed from live market data, FC26):**
- 200–999: multiples of 50
- 1000–9999: multiples of 100 (not 250 as initially assumed)
- 10000–99999: multiples of 250 (not 500 as initially assumed)
- 100000+: multiples of 1000
`_is_valid_fut_price()` encodes these corrected increments and is available as a utility, but is NOT applied in the live scraping path since the JS section-scoped extraction already ensures correct section targeting.

**Fodder sweep is now 2 page loads per full sweep (down from 26).**
`fodder_sweep` calls `fetch_fodder_all_ratings(pc)` then `fetch_fodder_all_ratings(console)`. `fetch_fodder_cheapest(rating, platform)` is retained as a standalone on-demand method for single-rating refreshes but is no longer called in the main sweep path.

### 2026-04-25 — session 12 — Phase 2d: Fodder tracker, card tagger, Ask LLM

**LLM model: claude-haiku-4-5-20251001.** Chosen for cost (~$0.00044/call at ~580 in / 240 out tokens) and speed. Temperature 0 for deterministic trade verdicts.

**Daily LLM spend cap enforced in application layer (0.50 USD default) as secondary safety net.** Config in `config/llm_config.yaml`. Tracked via `llm_calls` table. Cap is checked before every call; error message displayed in UI if exceeded.

**Fodder tracker polls FUT.GG cheapest-by-rating pages. Ratings 82-91.** URL: `?sort=cheapest&rating={N}`. 0-coin and <500-coin listings filtered as troll/extinct. Computes cheapest_bin, second_cheapest_bin, median_bin from first 5 valid prices. Runs every 30 min via scheduler. Both platforms swept sequentially (shared Playwright context).

**Card tagger uses rapidfuzz fuzzy matching at 85% threshold against card_aliases table.** Aliases seeded automatically from cards.player_name (full name, parts ≥5 chars, plus hard-coded common nicknames). Runs every 5 min via scheduler; processes signals with tagged_at IS NULL.

**Ask LLM feature is implemented in two places:** (1) Python `src/llm/ask.py` for standalone CLI testing/verification, (2) Electron main.cjs `db:askLLM` IPC handler for the UI (uses Node built-in fetch to call Anthropic API directly, no subprocess overhead). Both use the same system prompt and JSON verdict schema.

**LLM response may include markdown fences despite system prompt instructions.** Both Python and Node implementations strip `\`\`\`json...\`\`\`` fences before JSON.parse. This is a known Haiku 4.5 behavior.

### 2026-04-24 — session 10 — Phase 2c: Twitter, EA news, Reddit architecture

**Twitter uses Following-timeline single-page polling for <60s latency, not per-profile navigation.**
One navigation to `https://x.com/home` per 50s cycle covers all followed accounts simultaneously.
Per-profile navigation would require 5-6 page loads per cycle. The throwaway account follows only
the monitored leaker accounts, so the Following timeline is an exact filter.

**Twitter is a standalone Playwright worker (not a scheduler job) due to persistent browser.**
Like Discord, it maintains a long-lived process. Spawned by dev.ps1 and Electron main.cjs.
Toggle: `ENABLE_TWITTER_INGEST` env var (default true). Cookie file must exist at
`data/.cookies/x_cookies.txt` or the worker refuses to start.

**Reddit and EA news run as scheduler jobs inside the existing scheduler process.**
They're lightweight HTTP polling — no persistent connections. Added to `build_scheduler()` as
`IntervalTrigger` jobs alongside the FUT.GG jobs.

**Reddit JSON API returns 403 — credentials required.**
Reddit blocked unauthenticated `.json` API access in 2023. Current code raises `RedditAuthError`
immediately on 403 and writes a failure row to `scraper_health`. Reddit is blocked pending owner
setting up a free script app and providing `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` in `.env`.

**EA news uses RSS-first, HTML scrape fallback.**
Tried `https://www.ea.com/games/ea-sports-fc/news/rss` (and en-gb variant) first;
falls back to scraping the HTML news page with httpx + selectolax. EA's news page is
server-rendered (not a SPA), so httpx + selectolax is sufficient — no Playwright needed.

**`signal_category` and `priority` added to signals table (migration 0003).**
These fields allow the LLM (Phase 2d) to understand the provenance and urgency of each signal.
Both are populated at ingest time from `config/twitter_accounts.yaml` (for Twitter) or
from flair/listing type (for Reddit) or hardcoded 'news'/'high' (for EA).

### 2026-04-19 — session 5 — scheduler design decisions

**`AsyncIOScheduler` over `BackgroundScheduler`.**
Scrapers are all `async` (Playwright). `AsyncIOScheduler` runs jobs directly in the existing event loop — no `asyncio.run()` inside a thread needed. `BackgroundScheduler` would require thread-safe bridges into async code and is the wrong model.

**`build_scheduler()` extracted for testability.**
The scheduler factory does not call `.start()`. This lets unit tests inspect job registration (IDs, trigger types, intervals) without needing a live event loop running. `run()` (the entry point) calls `.start()` separately after `build_scheduler()`.

**Single shared Playwright browser + context across all scraper jobs.**
`FutGGScraper` is constructed once in `run()`, `__aenter__` called once, and passed as an arg to every job. Both PC and console jobs share the same browser context (staggered by 60s to avoid race). This avoids spawning a new Chromium on every 20-min tick. `__aexit__` called once on graceful shutdown.

**Windows SIGTERM fallback.**
`loop.add_signal_handler(SIGTERM, ...)` raises `NotImplementedError` on Windows. We wrap in `try/except (NotImplementedError, OSError)` and fall back to `signal.signal(SIGTERM, lambda ...)` for the Windows path. SIGINT works via `add_signal_handler` on both platforms.

**`taskkill /F /T /PID` in dev.ps1 for recursive kill.**
Python spawns Chromium as a grandchild. `Stop-Process -Force` only kills the direct child (the PowerShell wrapper). `taskkill /F /T` kills the entire process tree rooted at the backend PID, ensuring Chromium.exe is always cleaned up when the Electron window closes.

## 2026-04-25 Decisions

**Fodder covers ratings 81-93, all card versions, only 0-coin excluded.**
`fodder_snapshots` stores aggregate prices per rating/platform sweep. `fodder_cards` (migration 0005) stores top-10 individual card rows per snapshot with full per-card metadata (player_name, position, club_name, nation_name, club_badge_url, nation_flag_url, card_version, bin_price, rank_in_rating). Only cards where `_parse_price` returns `None` (EXTINCT/0/"") are excluded — no minimum price floor.

**FUT.GG `?sort=cheapest&rating=N` is a range bracket, not exact-match.**
Observed during TOTS season: ratings 81-90 returned the same cheapest card set because the market's cheapest gold cards in that range are the same heavily-supplied TOTS cards. Scraper stores what FUT.GG displays without interpretation — the fodder price per rating is the cheapest non-zero card on that page.

**Club badge and nation flag URLs not available on cheapest list page.**
`img[src*='club']` and `img[src*='nation']` selectors returned 0 matches. FUT.GG's cheapest list page does not embed club/nation images in the card anchor elements (they appear to be part of a SVG card art layer). Frontend uses `ImageWithFallback` component that shows a letter-initial placeholder on empty/failed URLs.
