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

### Electron reads SQLite directly via `better-sqlite3`
No IPC / API layer needed for Phase 1. Python writes. Electron reads. When UI needs to trigger an action (e.g. "force refresh this card," "dismiss this recommendation"), a small localhost FastAPI is added in a later phase — not before.

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

### 2026-04-19 — session 1 scaffolding decisions

**Electron main/preload use `.cjs` extension.**
`package.json` has `"type": "module"` (required for Vite ESM). Electron's `main` field must point to a CommonJS file; using `.cjs` extension lets Node treat it as CJS without requiring a separate `package.json` in the `electron/` folder.

**`migrate.py` uses `executescript()` throughout.**
Python's `sqlite3.Connection.execute()` rejects `DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))` even on SQLite 3.50 — the Python binding applies extra strictness on default values. `executescript()` passes the SQL directly to SQLite and works correctly. All DDL migrations must use `executescript`.

**`signal_card_tags` added as explicit join table.**
ARCHITECTURE.md described `tagged_cards` as a field on `signals`; implemented as a proper join table `signal_card_tags (signal_id, card_id)` for referential integrity and query efficiency.

**`_migrations` table lives in the same DB file.**
Migration state tracked in `_migrations` inside `data/fcpricemaster.db`. Kept alongside data (not a separate file) because the DB is single-file and gitignored; the table is idempotently created by both `0001_initial.sql` and `migrate.py`'s bootstrap step.
