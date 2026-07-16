# FCPriceMaster

A local-only AI trading advisor for the EA FC 26 Ultimate Team (FUT) transfer
market. FCPriceMaster scrapes live card prices from FUT.GG, ingests signals
from Discord trading communities, Twitter/X leakers, Reddit, and EA news, and
uses LLMs to generate buy/avoid recommendations for in-game coin trading.

**Not a financial tool.** FUT coins are in-game currency only; no real money
is involved in any recommendation. There is no in-game automation (no sniping,
no auto-bid, no auto-list) — the app gives advice, the player acts on it.
Runs entirely on the owner's machine; nothing is deployed publicly.

## How it works

- **Price pipeline** — a Playwright scraper reads public FUT.GG pages
  (trending, cheapest-by-rating, per-card detail, full paginated sweeps) into
  SQLite. Every price is validated against the FUT increment ladder and a
  tradeable/untradeable classifier so SBC cost estimates never masquerade as
  market prices. Schema guards write to a `scraper_health` table instead of
  failing silently.
- **Signals** — Discord (bot, allowlisted channels), Twitter/X (Playwright +
  session cookies), Reddit (OAuth), and EA news (RSS) land in a `signals`
  table, get classified (FUT market / IRL transfer / IRL result / promo leak),
  and are fuzzy-tagged against the card database.
- **Recommendations** — an APScheduler worker selects candidate cards
  (signal-mentioned, near 7-day lows, trending), enforces a 24-hour data
  staleness guard, and asks an LLM for buy/avoid calls with confidence and
  price targets. Outcomes are evaluated 24h later and scored.
- **Ask** — paste any trade call (text or screenshot) and query multiple
  models in parallel for verdicts.

## Multi-model AI

| Provider | Models | Cost |
|---|---|---|
| Anthropic | Claude Haiku | Paid, budget-capped per day |
| NVIDIA NIM | DeepSeek V4 Pro, Kimi K2.6, Qwen3 80B, Mistral Small (text + vision), GPT OSS 120B | Free (40 RPM) |

Any combination can be queried in parallel from the Ask view; scheduled
recommendation runs use the provider configured in `config/llm_config.yaml`.

## Stack

- **Backend:** Python 3.11+, `uv`, SQLite (WAL), httpx, Playwright,
  APScheduler, pydantic
- **Frontend:** Electron + Vite + React + TypeScript, `pnpm`,
  better-sqlite3 (direct DB reads via preload)
- **Design:** locked design system in `design.md` (atmospheric dark theme,
  OKLCH tokens, Bricolage Grotesque / Geist / Geist Mono)

## Setup

Prerequisites: Windows, Python 3.11+, Node 20+, [`uv`](https://docs.astral.sh/uv/),
`pnpm` (`npm i -g pnpm`).

```powershell
# one-time: install backend + frontend deps, Playwright Chromium
scripts\setup.ps1

# copy .env.example to .env and fill in your keys
# (Discord bot token, Anthropic key, NVIDIA key - all optional per feature)

# launch everything: scheduler + ingest workers + Electron app
scripts\dev.ps1
```

Per-worker toggles: `$env:ENABLE_DISCORD_INGEST="false"` or
`$env:ENABLE_TWITTER_INGEST="false"` before `dev.ps1`.

Tests: `cd backend; uv run pytest tests/ -q`

## Status

Phase 3 (autonomous recommendations) in progress. Phases 1 (price pipeline)
and 2 (signal ingestion + Ask) are complete. Phase 4 (supervised classifier
trained on logged outcomes) begins once enough outcomes accumulate.

This repository is public while the project is under active development and
will be made private again when the product is complete.
