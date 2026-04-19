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
