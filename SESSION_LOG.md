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
