# Day 1 prompt for Claude Code

## Instructions for the owner (you)

1. Copy all four `.md` files (`CLAUDE.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `SESSION_LOG.md`) into `C:\Claude Agent\FCPriceMaster\`.
2. Open a terminal in that directory.
3. Start Claude Code: `claude`
4. Paste the prompt below as your first message.
5. When the session ends (you're stopping for the day, or context is getting full), remind Claude Code to update `SESSION_LOG.md` and `ROADMAP.md` before you close it.

---

## The prompt (copy everything between the lines below)

---

I'm starting FCPriceMaster, a local-only AI trading advisor for EA FC26 Ultimate Team. I've placed `CLAUDE.md`, `ARCHITECTURE.md`, `ROADMAP.md`, and `SESSION_LOG.md` in this directory.

**Your task for this session:**  

1. Read `CLAUDE.md`, `ARCHITECTURE.md`, and `ROADMAP.md` end-to-end before writing any code. If anything is unclear or the files contradict each other, stop and ask me.

2. Execute **only ROADMAP Phase 1, sections 1.1 and 1.2**. Do not start 1.3 (scrapers) in this session.

3. For **1.1 Project scaffolding**:
   - Scaffold `backend/` and `frontend/` per the layout in `CLAUDE.md`.
   - Backend: `uv init`, Python 3.11+, `pyproject.toml` with dependencies stubbed (`httpx`, `selectolax`, `playwright`, `apscheduler`, `pydantic`, `pyyaml`), `ruff` and `mypy` configured.
   - Frontend: `pnpm create vite@latest frontend -- --template react-ts`, then add Electron (`electron`, `electron-builder`, `concurrently`, `wait-on` as dev deps), plus `better-sqlite3` and `recharts`.
   - Write `.gitignore` at the project root covering: `data/`, `node_modules/`, `__pycache__/`, `*.pyc`, `.venv/`, `.env`, `*.cookies`, `data/.cookies/`, `dist/`, `build/`, `.DS_Store`, `*.log`.
   - Write `scripts/setup.ps1` — runs `uv sync` in `backend/` and `pnpm install` in `frontend/`.
   - Write `scripts/dev.ps1` — launches the scheduler and the Electron dev server concurrently.
   - Verify: running `scripts/setup.ps1` completes cleanly on Windows, running `scripts/dev.ps1` opens the Electron window with a placeholder page.

4. For **1.2 Database layer**:
   - Write `backend/src/db/schema.sql` implementing exactly the tables listed in `ARCHITECTURE.md` (`cards`, `card_attributes`, `price_snapshots`, `signals`, `releases`, `recommendations`, `outcomes`, `scraper_health`, plus any join tables you need like `signal_card_tags`).
   - Include the `platform` CHECK constraint (`pc` or `console`) on `price_snapshots`.
   - Include a `game_edition` column on `cards` and `price_snapshots` for cross-FIFA portability.
   - Create `backend/src/db/migrations/0001_initial.sql` that applies the schema.
   - Write `backend/src/db/migrate.py` — a numbered migration runner that reads all `NNNN_*.sql` files in order, tracks applied migrations in a `_migrations` table, and idempotently applies new ones.
   - DB init enables WAL mode (`PRAGMA journal_mode=WAL;`) and sensible pragmas (`synchronous=NORMAL`, `foreign_keys=ON`).
   - Write Pydantic models in `backend/src/db/models.py` mirroring each table.
   - Write `backend/src/db/seed.py` that inserts 5 test cards: a mix of ratings, positions, both platforms. These exist so later we can verify the Electron dashboard reads end-to-end without live scrapers yet.
   - Verify: `uv run python -m backend.db.migrate` creates `data/fcpricemaster.db` with all tables, and `uv run python -m backend.db.seed` inserts the test rows without errors. Run `.tables` and a `SELECT` via `sqlite3 data/fcpricemaster.db` to confirm.

5. **Before ending the session:**
   - Append a new entry to `SESSION_LOG.md` using the template. Be specific in "Next" — name the first task of section 1.3 you'd want picked up next session.
   - Update `ROADMAP.md`: mark every completed task `[x]`; if anything is half-done mark it `[~]` with a note in the session log.
   - If you made any design decisions not covered in `ARCHITECTURE.md` (e.g., a specific Electron+Vite wiring approach, a migration-tracking table schema), add them to the "Decisions log" section of `ARCHITECTURE.md` with today's date.

**Out of scope for this session** — do not touch:
- Any scraper code (Phase 1.3)
- Any Electron UI beyond a placeholder renderer page that proves the app launches
- Any LLM integration
- Any Twitter/Discord/Reddit code

If you finish 1.1 and 1.2 with time to spare, stop and confirm with me before starting 1.3.

Go.
