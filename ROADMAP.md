# Roadmap

**Status legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked/needs owner input

Update status at the end of every session. Do not skip ahead — finish the current phase before starting the next. If a task seems to require a later-phase dependency, stop and ask.

---

## Phase 1 — Data pipeline foundation
**Goal:** price data flowing reliably from FUT.GG into SQLite, visible in a minimal Electron dashboard. No AI yet.
**Target:** 2–3 weeks of part-time work.

### 1.1 Project scaffolding
- [x] Initialize `backend/` with `uv`, `pyproject.toml`, Python 3.11+, `ruff` and `mypy` configured
- [x] Initialize `frontend/` with `pnpm`, Electron + Vite + React + TypeScript
- [x] Write `.gitignore` at project root (covers `data/`, `node_modules/`, `__pycache__/`, `.env`, `*.cookies`, `dist/`, `build/`, `.venv/`)
- [x] Write `scripts/setup.ps1` (installs backend and frontend deps end-to-end)
- [x] Write `scripts/dev.ps1` (launches backend scheduler + Electron in dev mode)
- [x] Verify clean launch on Windows, no errors — better-sqlite3 upgraded to v12.9.0; electron-rebuild wires native addon to Electron ABI; Vite + Electron launch confirmed

### 1.2 Database layer
- [x] Write `backend/src/db/schema.sql` matching the data model in ARCHITECTURE.md
- [x] Numbered migration runner at `backend/src/db/migrate.py` (reads `backend/src/db/migrations/NNNN_name.sql` in order)
- [x] WAL mode enabled on DB init
- [x] `backend/src/db/seed.py` inserts 5 test cards (mix of PC + console, different ratings) so reads can be verified end-to-end
- [x] Pydantic models mirroring each table in `backend/src/db/models.py`

### 1.3 FUT.GG scraper (first data source)
- [x] Inspect FUT.GG to identify the most stable endpoints — findings in `docs/futgg_endpoints.md`
- [x] Scraper approach decision: Playwright + DOM scraping of public pages only (no /api/*)
- [x] `backend/src/scrapers/base.py` — `SchemaGuardError`, `HttpxScraperBase`, `PlaywrightScraperBase` with CMP dismiss + platform switch + stealth
- [x] `backend/src/scrapers/futgg.py` implementing:
  - [x] `fetch_hot_cards(platform)` — trending list page, extracts position/rating/price from card badge DOM
  - [x] `fetch_card_prices(card_key, platform)` — card detail page, extracts current BIN price
  - [x] Schema validation on every card (SchemaGuardError + scraper_health on mismatch)
  - [x] Rate limiting (5–10s jitter, single shared Playwright context)
- [x] Persist results to `cards`, `card_attributes`, `price_snapshots`
- [x] Write success/failure to `scraper_health` on every run
- [x] `--once` CLI: `uv run python -m src.scrapers.futgg --once --platform pc --limit N`
- [x] 18 tests passing (pytest); live smoke test: 5 cards × 2 platforms → DB OK, both platforms populated
- [!] Known gap: `/players/trending/` shows ~30 cards. For 500-card coverage, need additional list pages or pagination — deferred to 1.3 extension after scheduler is wired (1.4)

### 1.4 Scheduler
- [x] `backend/src/workers/scheduler.py` using APScheduler AsyncIOScheduler
- [x] Jobs: FUT.GG trending every 20 min (PC at +30s, console at +90s); health prune daily 03:00 UTC
- [x] Graceful shutdown on SIGINT/SIGTERM (Windows fallback via signal.signal); drains in-flight jobs
- [x] Log to `data/logs/scheduler.log` with RotatingFileHandler (10MB, 5 backups)
- [x] Entry point: `uv run python -m src.workers.scheduler`
- [x] 8 scheduler tests passing (job registration, intervals, stagger, exception isolation, shutdown)

### 1.5 Electron dashboard (read-only)
- [x] Electron main process: spawns backend scheduler as child process on app launch (with a toggle in a settings panel to disable)
- [x] Preload exposes `better-sqlite3` queries to renderer safely (no arbitrary SQL from renderer)
- [x] Views:
  - [x] **Top Movers** — cards with biggest price change in last 24h, filterable by platform
  - [x] **Card detail** — search, then see price chart (recharts), recent snapshots, attributes
  - [x] **Scraper Health** — per-source last run, last success, failure streak, last error
- [x] Platform toggle (PC / Console) persistent in localStorage
- [x] Dark mode default

### 1.6 Phase 1 exit criteria
- [ ] 48h continuous run with zero silent failures (any failure visible in Scraper Health)
- [ ] ≥500 cards tracked per platform
- [ ] Dashboard renders price movement on real data
- [ ] `SESSION_LOG.md` kept current across all sessions
- [ ] Owner has walked through the dashboard and signed off before Phase 2 starts

---

## Phase 2 — Signal ingestion
**Goal:** Discord, Twitter, Reddit, EA news, fixtures all landing in `signals`. First LLM-assisted feature (the "Ask" mode).
**Target:** 2–3 weeks after Phase 1 sign-off.

### 2.1 Discord ingestion (Phase 2a) — COMPLETE
- [x] Phase 2a bugfix (session 9): forwarded message content extraction (MessageSnapshot API), dedup race condition (BEGIN IMMEDIATE), scraper timeout (45s→90s + domcontentloaded), dotenv warnings suppressed; 2 stale signals reprocessed with real content
- [x] Owner created Discord bot; token stored in `.env`
- [x] `discord.py` 2.7.1 client, read-only intents (guilds + guild_messages + message_content)
- [x] Bot reads only 3 allowlisted channels on owner's server; ignores all others
- [x] Forwarded messages parsed via `message_snapshots` API; direct messages tagged 'owner_direct'
- [x] `config/discord_sources.yaml` — channel ID → source_label, reliability, notes
- [x] Migration 0002: `source_server`, `original_author`, `original_ts_utc`, `has_attachments` on signals; `signal_attachments` table; `discord_message_ids` dedup table
- [x] Backfill on startup: last 100 messages per channel processed on connect
- [x] Graceful shutdown on SIGINT/SIGTERM (same Windows fallback pattern as scheduler)
- [x] Log to `data/logs/discord_ingest.log` (10MB rotating, 5 backups)
- [x] Signals view in dashboard: filter by source + time window, read-only, 60s auto-refresh
- [x] dev.ps1 spawns Discord worker; `ENABLE_DISCORD_INGEST=false` to skip
- [x] Electron main.cjs spawns/kills Discord worker; `settings.enableDiscordIngest` toggle
- [x] 33/33 tests passing (7 new discord tests + 26 existing)
- [x] Live smoke test: bot connected, all 3 channels visible (#src-free-server, #src-mitchy-duck, #src-miazaga)
- [ ] Card-name tagger: fuzzy-match message text against `cards` table, populate `signal_card_tags` — deferred to Phase 2b
- [!] Historical backfill beyond 100 messages — parked (owner decision)

### 2.2 Twitter/X ingestion via Playwright — COMPLETE
- [x] Owner exported session cookies to `data/.cookies/x_cookies.txt` (Netscape format, gitignored)
- [x] `config/twitter_accounts.yaml` — list of monitored accounts with category + priority metadata
- [x] `backend/src/utils/cookie_loader.py` — Netscape cookie file parser with session-cookie validation
- [x] `backend/src/workers/twitter_ingest.py` — polls `/home` (Following timeline) every 50s via Playwright
- [x] Tweet parsing via stable DOM selectors (`article[data-testid="tweet"]`, etc.)
- [x] Schema-guard: logs WARNING if 0 tweet articles found, ERROR after 5 consecutive empty polls
- [x] Login detection (URL contains `login`/`i/flow` → stop + error), rate-limit detection (empty-state element)
- [x] `twitter_tweet_ids` dedup table (same pattern as `discord_message_ids`)
- [x] Signals stored with `source_server=handle`, `signal_category`, `priority` from config
- [x] Migration 0003: `signal_category`, `priority` on signals; `twitter_tweet_ids`, `reddit_post_ids` dedup tables
- [x] `docs/twitter_sources.md` — monitored accounts, cookie refresh procedure, DOM selector reference
- [x] Twitter worker spawned by `scripts/dev.ps1` and `frontend/electron/main.cjs` (toggle via `ENABLE_TWITTER_INGEST`)
- [x] Live smoke test: 5 tweets ingested from Following timeline, health row OK
- [ ] Card-name tagger: fuzzy-match tweet text against `cards` table — deferred to Phase 2d LLM

### 2.3 Reddit ingestion — COMPLETE
- [x] `backend/src/workers/reddit_ingest.py` — httpx, no credentials; old.reddit.com JSON endpoint with Chrome UA bypasses 403
- [x] Subreddits: r/fut, r/EASportsFC, r/fut_economy — fetch new every 5 min, hot every 30 min
- [x] Dedup via `reddit_post_ids`; scraper_health on every run; schema-guard on malformed JSON
- [x] 10/10 reddit tests passing (incl. 2 httpx-mock tests for JSON parsing + signal insertion)
- [x] Live fetch verified: old.reddit.com returns real posts with correct JSON shape

### 2.4 EA news and fixtures
- [x] `backend/src/workers/ea_ingest.py` — RSS feed with HTML scrape fallback every 30 min via scheduler
- [x] EA news ingesting real articles (5 verified in smoke test)
- [ ] football-data.org free tier: fetch PL, La Liga, Serie A, Bundesliga, Ligue 1 fixtures daily
- [ ] Fixture-to-signal job: 48h before a headline match, emit an "anticipate matchup SBC" signal tagged with both squads' common/informs cards

### 2.5 "Ask" feature (first LLM integration) — Phase 2d COMPLETE
- [x] Fodder tracker: FUT.GG cheapest-by-rating pages, ratings 81-93, both platforms, every 30 min
- [x] `0005_fodder_cards.sql` — per-card detail rows linked to fodder_snapshots
- [x] `fetch_fodder_cheapest` rewritten: extracts top-10 cards per rating, no price floor (only 0-coin excluded), stores in `fodder_cards`
- [x] `db:getFodderByRating` + `db:getFodderHistory` IPC handlers; `FodderCard` TypeScript interface
- [x] Fodder view: expandable rows with horizontal card list (position pill, version label, fallback badge images, price), 7-day chart inline
- [x] `backend/src/db/migrations/0004_fodder.sql` — fodder_snapshots, card_aliases, llm_calls, tagged_at
- [x] `backend/src/workers/signal_tagger.py` — rapidfuzz 85% threshold, seeded aliases, every 5 min
- [x] `backend/src/llm/context_builder.py` + `ask.py` — Python CLI for standalone testing
- [x] Electron main.cjs `db:askLLM` IPC handler — Node fetch to Anthropic API, no subprocess
- [x] Daily spend cap (configurable via config/llm_config.yaml, default $0.50/day)
- [x] LLM calls logged to llm_calls table (model, tokens, cost_usd, input/output text)
- [x] Fodder dashboard view (table + 7-day line chart per rating)
- [x] Ask dashboard view (textarea, analyse button, verdict panel, history, cost tracker)
- [x] Real LLM test: "TOTW OOP Wirtz gold under 63K" → AVOID/85%/high risk ($0.000445)
- [x] Daily cap enforcement verified: correctly raises RuntimeError when exceeded
- [x] All 89 tests passing; selftest exits 0 with all handlers registered
- [x] **Session 14 bugfixes:** futgg_fodder 41-failure streak fixed — platform switched from Radix dropdown to URL param (`?sort=cheapest&rating=N&platform=pc|console`); no more timeout errors; 20 snapshots (10 PC + 10 console) confirmed. `fetch_card_on_demand` added; signal tagger triggers on-demand price fetch for newly tagged cards.
- [x] **Session 16 URL fix:** `?sort=cheapest&rating=N` ignored by FUT.GG — rewrote to `?overall__gte=N&overall__lte=N&sorts=current_price&platform=pc|console`; added `fetch_fodder_all_ratings` (single-page sweep, falls back gracefully to per-rating); verified both platforms with correct prices (82→400, 89→3300, 90→5400 PC).
- [x] **Session 19 rewrite:** `fetch_fodder_all_ratings` rewritten to use `page.evaluate()` JS DOM extraction on `/cheapest-by-rating/` — grandparent traversal finds all section cards in one DOM pass; anchor innerText format `name\nprice\nposition\nrating` parsed directly. 2 page loads per full sweep (was 26). `_is_valid_fut_price()` added with corrected increment ladder. Old stale data (3,295 snapshots, 32,415 cards) cleared. New sweep verified all 13 ratings, both platforms — no 38K+ values. 95/95 tests passing.

### 2.6 Phase 2 exit criteria
- [ ] All five sources ingesting reliably for a week
- [ ] Ask feature returns useful output on real examples
- [ ] Signal volume visible in dashboard

---

## Phase 3 — Autonomous recommendations
**Status:** In progress (session 23). Seeding outcome data now.

- [x] `generate_recommendations(platform, db_path, max_recs)` — selects top 20 candidates (3+ snapshots/48h, ranked by signal count), calls Claude Haiku, filters confidence<60 + holds, inserts buys/avoids
- [x] Fodder sweep (ratings 82-91) — within 10% of 7d low + promo in 14 days
- [x] `evaluate_outcomes(db_path)` — marks recs >24h old as correct/incorrect/neutral/expired
- [x] Scheduler jobs: recommendations_pc (every 2h), recommendations_console (offset 60min), outcome_evaluator (every 6h)
- [x] HTTP trigger server on 127.0.0.1:8765 (POST /run-recommendations) for UI-initiated runs
- [x] IPC handlers: getRecommendations, dismissRecommendation, getRecommendationStats, triggerRecommendations
- [x] Recommendations view in UI: stats bar, buy/avoid cards, dismiss, outcome badge, auto-refresh 60s
- [ ] Walk through UI with owner sign-off
- [ ] Accumulate ≥500 outcomes to seed Phase 4 classifier

---

## Phase 4 — Feedback loop and supervised classifier
**Placeholder.** Only begins once ≥500 logged outcomes exist.
- Outcome evaluator job
- Feature engineering
- Train XGBoost or similar on `features → direction`
- Classifier runs alongside LLM; disagreements flagged in UI

---

## Parking lot (ideas, not yet scheduled)
- Cross-platform arbitrage detection (PC vs console price gaps, within trading constraints)
- Backtesting harness over historical signals
- SBC solver integration
- Alerting via Discord webhook or local notifications
