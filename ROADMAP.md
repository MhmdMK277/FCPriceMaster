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
- [ ] Electron main process: spawns backend scheduler as child process on app launch (with a toggle in a settings panel to disable)
- [ ] Preload exposes `better-sqlite3` queries to renderer safely (no arbitrary SQL from renderer)
- [ ] Views:
  - [ ] **Top Movers** — cards with biggest price change in last 24h, filterable by platform
  - [ ] **Card detail** — search, then see price chart (recharts), recent snapshots, attributes
  - [ ] **Scraper Health** — per-source last run, last success, failure streak, last error
- [ ] Platform toggle (PC / Console) persistent in localStorage
- [ ] Dark mode default

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

### 2.1 Discord ingestion
- [ ] Owner creates a Discord bot account, joins trading servers with it
- [ ] `discord.py` client, read-only intents
- [ ] Log every message from configured channels into `signals`
- [ ] Card-name tagger: fuzzy-match message text against `cards` table, populate `signal_card_tags` join
- [ ] Config: `config/discord_servers.yaml` lists server IDs and channel IDs

### 2.2 Twitter/X ingestion via Playwright
- [ ] One-time: owner creates throwaway X account, extracts session cookies, stores in `data/.cookies/x.json` (gitignored)
- [ ] Playwright script polling FUT Sheriff, FUT Scoreboard, FUT Donkey, others (list in `config/twitter_accounts.yaml`) every 10 min
- [ ] Parse new tweets out of DOM, schema-guard, insert into `signals` with `source='twitter'`
- [ ] Card tagger reused from Discord

### 2.3 Reddit ingestion
- [ ] `praw` bot, read-only
- [ ] Pull new posts and top comments from r/FUT_Economy and r/EASportsFC every 30 min
- [ ] Separate signal_type for meta-discussion vs price/trade specific

### 2.4 EA news and fixtures
- [ ] EA FC news RSS (or scrape if no RSS) every hour
- [ ] football-data.org free tier: fetch PL, La Liga, Serie A, Bundesliga, Ligue 1 fixtures daily
- [ ] Fixture-to-signal job: 48h before a headline match, emit an "anticipate matchup SBC" signal tagged with both squads' common/informs cards

### 2.5 "Ask" feature (first LLM integration)
- [ ] New Electron view: paste text (tweet, Discord message, or free-form question)
- [ ] Backend endpoint that pulls current price context for mentioned cards, sends to Claude API with a structured prompt, returns JSON verdict
- [ ] Daily spend cap (configurable, default $0.50/day)
- [ ] All responses logged to `recommendations` with `source='ask'`

### 2.6 Phase 2 exit criteria
- [ ] All five sources ingesting reliably for a week
- [ ] Ask feature returns useful output on real examples
- [ ] Signal volume visible in dashboard

---

## Phase 3 — Autonomous recommendations
**Placeholder.** Scope decided after Phase 2 observations. Likely shape:
- Recurring job that scans hot-list cards, pulls signal context, asks LLM for calls
- Recommendations view in UI with filters and dismissal
- Optional Discord bot to post to owner's private server

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
