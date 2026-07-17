---
name: fcpricemaster
description: "FCPriceMaster domain knowledge and non-negotiables: FUT market rules, valid BIN price detection, untradeable card handling, the multi-model LLM provider system, and project conventions. Read this before doing any work in this repo — especially anything touching prices, scrapers, recommendations, or LLM calls."
---

# FCPriceMaster — project skill

Local-only AI trading advisor for EA FC 26 Ultimate Team (FUT). Scrapes card
prices from FUT.GG, ingests signals (Discord, Twitter/X, Reddit, EA news), and
generates buy/avoid recommendations via LLMs. Coins are in-game currency; no
real money is involved and none of this is financial advice.

## Read first, in order
1. `CLAUDE.md` — rules, stack, commands
2. `ROADMAP.md` — current phase
3. `SESSION_LOG.md` — newest entry (top of file)
4. `design.md` — locked UI design system (Hallmark-managed; do not restyle ad hoc)

## FUT domain knowledge (internalize before touching data)

### Tradeable vs untradeable
- A card has a real BIN (Buy It Now) price **only if it is tradeable** on the
  transfer market. Untradeable cards come from SBCs (Squad Building
  Challenges) or Objectives.
- FUT.GG shows tradeable cards with a **Prices tab and a Price Momentum
  section (Lowest BIN)**. Untradeable cards show **neither** — only an SBC
  cost estimate (the value of fodder needed to complete the challenge).
- **An SBC cost estimate is NOT a BIN price. Never store it as one.**
  This bug once poisoned 103,240 snapshots (purged in migration 0011).
- Version names containing `SBC`, `Objective`, `Objectives` are almost always
  untradeable — but FUT.GG often uses promo names (`TOTS HM`) instead, so
  version-name matching alone is insufficient. The authoritative check is the
  detail page (`_page_is_tradeable` in `backend/src/scrapers/futgg.py`).
- EXTINCT (no price shown) never proves untradeable — tradeable cards can be
  temporarily extinct. Tradeability is three-state: 0 / 1 / None (no evidence).
  See `_classify_tradeable`.

### The FUT price increment ladder (a valid BIN must match)
| Range | Increment |
|---|---|
| under 1k | 50 |
| 1k–10k | 100 |
| 10k–50k | 250 |
| 50k–100k | **500** |
| 100k+ | 1000 |

Any price off this ladder is an SBC cost estimate or scrape artifact, not a
BIN. Canonical implementation: `_is_valid_fut_price` / `_is_real_bin_price`
in `backend/src/scrapers/futgg.py`. (The 50k–100k band is 500s, not 250s —
a session-36 fix; don't regress it.)

### Card vocabulary
- TOTS = Team of the Season, TOTW = Team of the Week, TOTY = Team of the Year,
  POTM = Player of the Month; Icon and Hero are special high-value cards.
  All of these are tradeable *unless* they are SBC/Objective versions.
- Fodder = high-rated cards used as SBC input; tracked per rating (81–93) in
  `fodder_snapshots` / `fodder_cards`.

### Data freshness
- The market moves fast. **Price data older than 24h must not drive
  recommendations.** The recommender enforces `STALE_THRESHOLD_HOURS = 24`
  and `MIN_SNAPSHOTS = 3` (`backend/src/llm/recommender.py`); never bypass
  the staleness guard, and always surface data age to the LLM ("recorded
  N.Nh ago").
- Two markets: `pc` and `console`. Every price row and every query is
  platform-tagged. Never mix them.

### Timestamps
- **All DB timestamps are UTC in `YYYY-MM-DDTHH:MM:SSZ` (T-format).**
  A space-format `datetime('now')` in a WHERE clause silently matches nothing
  against T-format columns — this exact class of bug killed recommendations
  for 38 days. Use `strftime('%Y-%m-%dT%H:%M:%SZ', 'now')` in SQL.

## Multi-model LLM provider system
- Providers: Anthropic Claude Haiku (paid, budget-capped) + 5 free NVIDIA NIM
  models (OpenAI-compatible, one `NVIDIA_API_KEY`):
  `deepseek-ai/deepseek-v4-pro` (slow cold-start, 60-120s),
  `moonshotai/kimi-k2.6` (**TEMPORARILY UNAVAILABLE** — NVIDIA endpoint 404
  "Function not found for account" as of 2026-07-17, even though /v1/models
  still lists it. Kept in registry; the 30-min health probe greys it in the
  UI and auto-restores it when NVIDIA re-enables the endpoint. Health-check
  NVIDIA models with a real completion, not the model list),
  `qwen/qwen3-next-80b-a3b-instruct`, `mistralai/mistral-small-4-119b-2603`
  (also the vision model), `openai/gpt-oss-120b` (reasoning model — needs
  `max_tokens >= 1500` or content comes back empty).
- Python side: `backend/src/llm/providers/` (registry + base). Node side: the
  Ask UI calls providers directly from Electron `main.cjs` via fetch — the Ask
  flow never touches Python.
- Scheduled recommendation runs read `scheduled_provider` from
  `config/llm_config.yaml`. Haiku spends real money and is budget-capped
  (`daily_cap_usd`); NVIDIA models are free (40 RPM).
- 120s timeout on NVIDIA calls in BOTH main.cjs and nvidia_provider.py.

## Non-negotiables (from CLAUDE.md, enforced)
- Never commit secrets, cookies, `.env`, or the SQLite DB. `x_com_cookies.txt`
  leaked once and was purged from git history — do not let any cookie or key
  file near the index.
- Every scraper has a schema guard; failures write to `scraper_health`,
  never silently return stale data.
- No in-game automation (sniping, auto-bid, auto-list). Advice only.
- Cross-FIFA portable: card attributes are tag-based rows; source URLs live
  in `config/sources.yaml`.
- End every session by updating `SESSION_LOG.md` (newest entry at TOP),
  `ROADMAP.md`, and `ARCHITECTURE.md` (if decisions were made).
- The owner does not run verification commands — verify everything yourself
  and report results.

## Stack and commands
- Backend: Python 3.11+ / uv / SQLite WAL / Playwright / APScheduler.
  Frontend: Electron + Vite + React + TS / pnpm / better-sqlite3.
- Run from `backend/`: tests `uv run pytest tests/ -q` (170+ must pass);
  migrations `uv run python -m src.db.migrate`; one-off scrape
  `uv run python -m src.scrapers.futgg --once --platform pc --limit 5`.
- Frontend from `frontend/`: `pnpm build` (tsc + vite), `pnpm selftest`.
- Full dev launch: `scripts\dev.ps1` (spawns scheduler + Discord + Twitter
  workers + Electron; dev.ps1 owns worker spawning — Electron only spawns
  workers when `AUTO_START_BACKEND` is not `false`).
- Scheduler HTTP trigger: `POST 127.0.0.1:8765/run-recommendations`. If the
  port is taken the scheduler exits loudly (`os._exit(1)`) — a bound port
  means exactly one scheduler owns it. Test with Node fetch, not curl.

## UI design system
The frontend is Hallmark-managed (`design.md` at root is the locked system:
atmospheric genre, custom "Coin" gold theme, Bricolage Grotesque + Geist +
Geist Mono, tokens in `frontend/src/tokens.css`). Every colour and font goes
through `var(--token)` — no inline hex, no new fonts, no coloured left-edge
stripes, no emoji as icons. Numeric data always uses `--font-mono` with
`tabular-nums`.
