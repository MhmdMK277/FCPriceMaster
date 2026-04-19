# FCPriceMaster — Claude Code Context

Local-only AI trading advisor for EA FC26 Ultimate Team transfer market.
Runs on Windows, single user (the owner). Never deploy publicly.

---

## Read before every session (in this order)
1. **This file (CLAUDE.md)** — rules and stack
2. **ROADMAP.md** — current phase, open tasks, what's done
3. **SESSION_LOG.md** — latest entry, to see where we stopped and why

## Update at end of every session (non-negotiable)
1. Append a new entry to **SESSION_LOG.md** using the template at the bottom of that file. Fill in: date, goal, done, next, gotchas, changed files.
2. In **ROADMAP.md**, mark completed tasks `[x]`, in-progress `[~]`, blocked `[!]`.
3. If any architectural decision was made, add a short note to **ARCHITECTURE.md** under a dated "Decisions" heading.

If a session is interrupted (context window fills, user stops mid-task), still update the log with what exists and what's half-done. Never leave the log stale.

---

## Project root
`C:\Claude Agent\FCPriceMaster`

## Directory layout
```
backend/     Python workers, scrapers, scheduler, DB layer
frontend/    Electron + React + Vite app
data/        SQLite DB file (gitignored)
docs/        Deeper design notes (add as needed)
scripts/     PowerShell launchers (setup.ps1, dev.ps1)
config/      YAML configs (release_calendar.yaml, sources.yaml)
```

---

## Stack
- **Backend:** Python 3.11+, SQLite with WAL mode, `httpx` + `selectolax` for static scraping, Playwright for JS-heavy sites (Twitter/X), APScheduler for recurring jobs, `pydantic` for config and data models
- **Frontend:** Electron + Vite + React + TypeScript, `better-sqlite3` for direct DB reads from renderer (via preload)
- **LLM (later phases):** Claude API (primary, via Anthropic SDK) and Ollama/Llama 3.1 8B as local fallback on the secondary PC (i7-9700K, RTX 2060 Super, 32GB DDR4)
- **Package managers:** `uv` for Python, `pnpm` for Node
- **Linting/formatting:** `ruff` + `mypy` for Python, `biome` or ESLint + Prettier for TS

---

## Non-negotiables
- **Never commit** secrets, session cookies, `.env`, or the SQLite DB. `.gitignore` must cover these.
- **Every scraper has a schema guard.** If the source HTML/API structure changes, raise loudly and write a failure row to `scraper_health`. Do NOT silently return stale data.
- **All times stored in UTC** in the DB. Convert to the user's local time only at display.
- **No in-game automation** (auto-bid, sniping, auto-list). Recommendations only. This is a hard ethical/ToS line.
- **Two separate markets:** `pc` and `console` (PlayStation + Xbox share). Every price row must tag which. Every UI view must be filterable by platform.
- **Cross-FIFA portable:** card attributes are tag-based (playstyles stored as rows in `card_attributes`, not hardcoded columns). Source URLs live in `config/sources.yaml`, not in code.
- **Single-click launch** is a product goal. Electron app is the entry point; it spawns the Python scheduler as a child process. User should not have to touch a terminal to run the system in normal use.

---

## Commands
- First-time setup: `scripts\setup.ps1`
- Dev launch (backend + frontend): `scripts\dev.ps1`
- Run scrapers once (manual): `uv run python -m backend.scrapers.futgg --once`
- Apply DB migrations: `uv run python -m backend.db.migrate`
- Seed test data: `uv run python -m backend.db.seed`

---

## Current phase
See **ROADMAP.md**. Do not skip ahead — finish the current phase's tasks before starting the next. If a task seems to require something from a later phase, stop and ask the user.

---

## Working style
- Prefer small, verifiable steps. After each meaningful change, confirm it runs.
- When a design decision has more than one reasonable path, stop and ask rather than silently picking. Record the chosen path in ARCHITECTURE.md.
- If any of the `.md` files contradict each other or something is unclear, halt and ask the user. Do not assume.
