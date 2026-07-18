# Session Log

Append new entries at the **top** (newest first). Every session must end with a new entry.

Required fields per entry: date, session number, goal, done, next, gotchas, changed files.

This file holds sessions 31 and later. Sessions 1–30 live in **SESSION_LOG_ARCHIVE.md**.

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

### 2026-07-18 — session 39
**Goal:** Fix the concurrent recommendation dedup race (scheduled + manual runs both pass the 10h guard before either writes).

**Done:**
- **Process-level rec lock:** `_rec_lock = asyncio.Lock()` + `run_recommendations_safe()` in scheduler.py; both `job_recommendations` (scheduled) and `_run_recs_with_result` (HTTP trigger) go through it. A second caller returns IMMEDIATELY — `locked()` pre-check instead of the blocking `async with`-only pattern, since the requirement is skip-not-queue (race-free: single event loop, uncontended acquire doesn't yield). HTTP skip response `{"status": "skipped", "skipped": true, "reason": "recommendation run already in progress", "recs_added": 0}` keeps `skipped: true` so the existing Recommendations toast path works unchanged.
- **Live verification:** two POSTs 0.1s apart — call A ran (9.5s, 3 recs), call B skipped in 0.6s with the exact reason string. Bonus: the scheduler's own startup `recommendations_pc` job fired at +30s during call A and logged `JOB SKIP — run already in progress` — the precise session-38 race, deflected live. No same-card duplicates in the window (session 38 had avoid+buy ids 1140/1142).
- **Date-dependent test fix:** FUTTIES went ACTIVE today (`futties_window_start: "07-18"`), so `_futties_85_recommendation` began appending a structural rec and broke `test_generate_recommendations_filters_hold_and_low_confidence` (assert 2 == 1) — a latent bug that surfaced at date rollover, unrelated to the lock. Patched `_calendar_context` in that test to a neutral calendar. 174/174 pass.

**Next:** Owner UI walkthrough (Phase 3 sign-off) still pending. Watch for other tests reading the real release_calendar.yaml — any exact-count assertion can break when a promo window opens (only this one failed today).

**Gotchas:**
- The lock is per-process only — correct because port 8765 enforces a single scheduler; direct `generate_recommendations` calls from standalone scripts (like session 38's test) bypass it.
- FUTTIES is active as of today: every run now appends the structural 85-fodder rec and reasonings lean avoid-gold; expected behavior, not a regression.

**Changed files:**
- `backend/src/workers/scheduler.py` (_rec_lock, run_recommendations_safe, both call sites)
- `backend/tests/test_recommender.py` (calendar isolation in the filter test)
- `SESSION_LOG.md`, `ROADMAP.md`

### 2026-07-18 — session 38
**Goal:** Kimi health probe (keep in registry, grey in UI), context-builder name-collision fix, fodder hallucination guard, skill wording update, verify + push.

**Done:**
- **NVIDIA health probe (Fix 1):** `health_check()` on `NvidiaProvider` (inherited by text + vision providers); `probe_all_providers()` in registry (parallel asyncio.gather, Anthropic assumed up when key set); scheduler `provider_probe` job every 30 min caching into `_provider_health` + new `POST /probe-providers` endpoint serving the cache (probes inline only when cache empty); main.cjs fetches on app ready (15s retry loop until scheduler binds :8765, then 30-min cadence) into module-level `providerHealth` Map; `db:getProviderHealth` IPC + preload + electron.d.ts; Ask.tsx chips and Recommendations.tsx model dropdown grey unhealthy providers with "Temporarily unavailable — NVIDIA endpoint offline" tooltip, "(offline)" suffix, and 60s renderer re-poll for auto-restore. Select-all skips unhealthy.
- **Probe semantics (deviation from spec, measured):** spec said max_tokens=1 / 5s timeout / timeout=unhealthy — that fails its own acceptance criteria. Measured: pulled endpoints 404 in 0.2s; DeepSeek cold-start 55.1s; reasoning models stall with max_tokens ≤512 (hang >20s) but answer in 1–3.4s at 2000. Final probe: max_tokens=2000, 10s timeout, HTTP error ⇒ unhealthy, timeout ⇒ healthy (cold ≠ pulled). Live result: `{haiku:true, deepseek:true, kimi-k2-6:false, qwen3:true, mistral-small:true, gpt-oss:true, mistral-vision:true}`.
- **Name-collision fix (Fix 2):** root cause was NOT a signal_card_tags query (that table was already card_id-joined) — the recommender passed `card["player_name"]` into `build_context`, whose `_match_cards` substring match returned all 8 Bellingham versions with the 2170.8h-old legacy TOTS seed first. Added `card_id` pin parameter to `build_context`; recommender passes it. Verified: pinned context returns exactly one card per id; Summer Stars gets its own 0.0h-fresh context. Live recs now cite real per-card ranges (Mbappé "7.8M high" vs 7.0M snapshot) instead of phantom staleness.
- **Fodder hallucination guard (Fix 3):** drop any fodder buy whose `suggested_buy_price > cheapest_now * 10` with a warning log (uses the same snapshot value the prompt was built from); prompt gains "prices are raw coin values, never thousands/850K" instruction.
- **Skill wording (Fix 4):** Kimi entry now TEMPORARILY UNAVAILABLE (404 since 2026-07-17, kept in registry, auto-restores via probe).
- **Verification:** 174/174 tests (×2 runs); `pnpm build` clean; probe endpoint JSON matches acceptance exactly; collision test script proves old-vs-new behavior; live mistral-small run → 3 player AVOIDs (Saibari/Mbappé/Messi Summer Stars, 0.0h-fresh data) + fodder BUYs 800/2,700/17,500 all ladder-valid, no millions; screenshot confirms "Kimi K2.6 (offline)" greyed chip with others selectable.
- Dev tree restarted twice (scheduler endpoint, then probe-semantics fix) — required by the fixes.

**Next:** Owner UI walkthrough (ROADMAP Phase 3 sign-off) — and watch the 30-min probe log line (`JOB DONE provider_probe — unhealthy: …`) to see Kimi auto-restore when NVIDIA re-enables it. Consider a dedup guard for concurrent scheduled + manual recommendation runs (observed avoid+buy pair on the same card from two overlapping runs, ids 1140/1142).

**Gotchas:**
- Reasoning models (gpt-oss, deepseek) hang on tiny max_tokens instead of erroring — never probe with max_tokens=1.
- A probe timeout is cold-start evidence, not a pulled endpoint; only fast HTTP errors mean "gone".
- The scheduler HTTP endpoint runs `job_probe_providers` inline when its cache is empty, so an early UI fetch can log two overlapping `JOB START provider_probe` lines at boot — harmless (idempotent cache write).
- `signal_card_tags` has no `id` column (composite signal_id+card_id only) — COUNT(sct.id) fails; count signal_id.

**Changed files:**
- `backend/src/llm/providers/nvidia_provider.py` (health_check)
- `backend/src/llm/providers/registry.py` (probe_all_providers)
- `backend/src/workers/scheduler.py` (provider_probe job, /probe-providers endpoint, _provider_health cache)
- `backend/src/llm/context_builder.py` (build_context card_id pin)
- `backend/src/llm/recommender.py` (card_id passthrough, fodder guard, fodder prompt instruction)
- `frontend/electron/main.cjs` (providerHealth Map, refresh loop, db:getProviderHealth)
- `frontend/electron/preload.cjs`, `frontend/src/electron.d.ts` (getProviderHealth)
- `frontend/src/views/Ask.tsx`, `frontend/src/views/Recommendations.tsx` (greyed unhealthy providers)
- `.claude/skills/fcpricemaster/SKILL.md` (Kimi wording)
- `ROADMAP.md`, `SESSION_LOG.md`

### 2026-07-18 — session 37
**Goal:** Session 36 follow-up: verify scheduler + sweeps after launch, freshness check, Kimi K2.6 recommendation run with staleness-guard verification, split SESSION_LOG, screenshot the redesigned UI.

**Done:**
- App was NOT running at session start (last log entry June 13) — launched via `dev.ps1`, all 17 scheduler jobs registered cleanly (trending, tier 1–4, full card sweep PC+Console at 06:00/06:30 UTC, fodder sweep, signals, outcome evaluator).
- **BUG FIX:** `job_recommendations` crashed with `NameError: _load_config is not defined` (scheduler.py:138) — session 36 added the `scheduled_provider` lookup but never imported `_load_config` from `src.llm.ask`. Every scheduled (cron 08:00 UTC) recommendation run would have died. Added the import; 174/174 tests pass; restarted the dev tree and confirmed the job now runs through to the staleness-guard path without error.
- Freshness: 29 distinct cards with snapshots <2h old (28 pc / 27 console) out of 4,132 total — only trending+fodder jobs had run; tier sweeps refill coverage on their hourly/daily cadence.
- **Kimi K2.6 is DEAD on NVIDIA NIM** — every call 404s with "Function '23d4f03a…' not found for account", even though `/v1/models` still lists `moonshotai/kimi-k2.6`. Verified all other 4 models work (mistral-small 0.4s, deepseek 1.6s, gpt-oss 1.8s, qwen3 19s). Ran recommendations with `mistral-small` instead.
- Staleness guard verified live: the 33-day-old cards (Rice/Hall/Martínez TOTS, ~856h) logged "Staleness guard: … skipping"; fresh trending cards were initially excluded with "only 1/2 snapshots" until a third scrape pass landed.
- Recommendation run via `POST /run-recommendations` (mistral-small): 13 recs — 3 player (Riquelme Unbreakables Icon BUY @370,000 ✓ ladder-valid, tradeable, 0.1h-fresh; Bellingham + Bruno Guimarães Summer Stars AVOID) + 10 fodder BUYs (targets match fodder_snapshots week lows exactly, all ladder-valid).
- **Dismissed 1 bad rec:** fodder rating-88 target 850,000 — model hallucinated "850k" from a prompt that correctly said 850 coins (1000× error). Marked dismissed with reason in DB (rec id 1127).
- Split SESSION_LOG.md: sessions 1–30 → SESSION_LOG_ARCHIVE.md, 31+ stay here; CLAUDE.md updated to reference both.
- Screenshot of Electron UI captured via PrintWindow (owner was in a fullscreen game; no focus steal). Ask page renders the Coin gold theme correctly: dark atmospheric background, gold accents on brand mark/active nav/platform toggle/Analyse button, provider chips color-coded (Haiku purple, NVIDIA green), mono numerals.

**Next:** Fix the LLM context builder: `_format_card_message` takes `context["mentioned_cards"][0]` matched by player name, so a different version of the same player can supply the "Last price / data_age_hours" line (Bellingham Summer Stars got the 2170h-old legacy TOTS seed's age; Bruno Guimarães got 856h). Both produced safe AVOIDs, but a fresh card can be smeared as stale — or worse, vice versa. Also decide what to do with the dead Kimi provider (remove from registry + UI chips + llm_config, or keep behind a health check).

**Gotchas:**
- NVIDIA `/v1/models` listing a model does NOT mean the account can invoke it — Kimi 404s at `/chat/completions` with a "Function not found for account" body. Health-check with a real completion, not the model list.
- `scheduled_provider` in llm_config.yaml is still `gpt-oss-120b` (works). The Kimi chip in the Ask UI will fail if selected.
- Scheduler log is append-mode with 10MB rotation; today's run starts at line ~63288. "First 50 lines" of the file are June 8.
- Mistral Small at temp 0 hallucinated "850k" from "850" in the fodder prompt — fodder targets need a sanity clamp against the prompt's own cheapest_bin (e.g. reject target > 3× cheapest).

**Changed files:**
- `backend/src/workers/scheduler.py` (added `from src.llm.ask import _load_config`)
- `SESSION_LOG.md` (split: 31+ here), `SESSION_LOG_ARCHIVE.md` (new: sessions 1–30)
- `CLAUDE.md` (references both log files)
- `.claude/skills/fcpricemaster/SKILL.md` (Kimi K2.6 marked dead on NVIDIA NIM)
- DB: dismissed rec id 1127 (hallucinated 850k fodder target)

### 2026-07-16 — session 36
**Goal:** Full audit + course correction + forward execution (project takeover session): verify Session 35 landed, fix what the audit finds, Hallmark UI redesign, project SKILL.md, README, security scan, GitHub push.

**Done:**
- **Audit:** Session 35 WAS fully executed (migrations 0011/0012 applied in DB, staleness guard + sweep jobs + tradeable classifiers live in code, sweeps grew coverage 2,049 → 4,855 cards / 3,976 tradeable). 170/170 tests passed pre-fix. No ghost processes; port 8765 free. Everything stale since June 13 because the app simply hasn't been launched — not a code bug; staleness guard correctly blocks recs on stale data.
- **BUG FIX (increment ladder):** `_is_valid_fut_price` allowed %250 across 10k–100k; the real ladder is 250 for 10k–50k and **500 for 50k–100k**. 26 SBC estimates (59,250 / 97,750 / 73,250-style) leaked through post-session-35. Fixed the ladder, added 4 test cases for the 50k–100k band, purged the 26 rows. 174/174 tests pass.
- **BUG FIX (timestamp format):** `evaluate_outcomes` wrote `datetime('now')` (space format) into `outcomes.evaluated_at_utc` — same format-mismatch class as the 38-day recommender outage. Switched both INSERTs to `strftime('%Y-%m-%dT%H:%M:%SZ','now')`; normalized the 6 existing rows.
- **BUG FIX (dead scheduled recs):** scheduled `job_recommendations` always used the default provider (haiku) — with $0 Anthropic balance every scheduled run since session 30 produced nothing (only 6 recs exist, all manual NVIDIA). Added `scheduled_provider` to `config/llm_config.yaml` (set to `gpt-oss-120b`, free + verified end-to-end in session 34); scheduler reads it.
- **Repo state fix:** working-copy ROADMAP.md had regressed to a pre-session-28 version (mtime June 28) deleting the sessions 28–35 records — restored from HEAD, since the deleted lines document work verifiably present in the code.
- **Live pipeline check:** `futgg --once --limit 5` works against current FUT.GG (Festival of Football promo cards scraped, health row OK).
- **Hallmark redesign:** installed nutlope/hallmark; audit found 5 critical / 5 major / 3 minor tells (Tailwind-slate default palette, single system font, zero tokens, side-stripe cards ×3, suppressed focus, 52 inline style blocks in Recommendations.tsx, emoji as icons). Redesigned as a design.md-managed app: atmospheric genre, custom "Coin" theme (gold accent over warm near-black, OKLCH tokens), Bricolage Grotesque + Geist + Geist Mono (self-hosted via @fontsource — CSP blocks CDNs), tabular-nums on all data, :focus-visible everywhere, elevation by lightness, two fixed canvas blooms, one view-mount fade. New `design.md`, `frontend/src/tokens.css`, rewritten `index.css`/`App.css`, Recommendations.tsx converted to classes, remaining hex in all views token-swapped. `pnpm build` clean; visually verified in browser (Ask, Recommendations, Top Movers screenshots).
- **Project skill:** `.claude/skills/fcpricemaster/SKILL.md` — FUT domain rules (BIN validity ladder, untradeable detection, staleness, T-format timestamps), provider system quirks, non-negotiables.
- **Security:** full-history scan — `.env`/DB never committed; `nvidia_credentials/*.py` contain model IDs only; `.env.example` placeholders only. `x_com_cookies.txt` WAS in history (added f6bab88, untracked c1854c3) — **purged from all history with git filter-repo and force-pushed**; owner should still invalidate that X session (log out everywhere / rotate password) since the repo was public. Redacted a truncated `nvapi-4wnM...` key prefix from old SESSION_LOG entries.
- **README.md** written (project intro, stack, setup, multi-model table, status, re-private note; no license file by design).

**Next:** LAUNCH THE APP (`scripts\dev.ps1`) — all data is 33 days stale and refreshes only while the scheduler runs; the 06:00/06:30 UTC sweeps rebuild coverage overnight. Then owner visual pass on the redesigned UI. Consider archiving SESSION_LOG entries 1–30 to `docs/session-archive.md` (file is 134KB and keeps growing; see MD-structure note in this session's report).

**Gotchas:**
- The FUT ladder's 50k–100k band is 500s, not 250s. Migration 0011 and the session-35 scraper both encoded 250 — if you see %250-but-not-%500 prices in that band, they are SBC estimates.
- `outcomes.evaluated_at_utc` was the only space-format column left; everything is T-format now. Keep it that way.
- Scheduled recs now follow `scheduled_provider` in llm_config.yaml. Set it back to `haiku` only after the Anthropic account has credits.
- git history was rewritten (filter-repo) on 2026-07-16 — any old clone must be re-cloned, not pulled.
- Frontend fonts are bundled locally (@fontsource); the index.html CSP blocks font CDNs by design.

**Changed files:**
- `backend/src/scrapers/futgg.py`, `backend/tests/test_futgg.py` (ladder fix + tests)
- `backend/src/llm/recommender.py` (outcome timestamp format)
- `backend/src/workers/scheduler.py`, `config/llm_config.yaml` (scheduled_provider)
- `design.md`, `.hallmark/log.json`, `.agents/skills/hallmark/` (new)
- `frontend/src/tokens.css` (new), `frontend/src/index.css`, `frontend/src/App.css` (rewrites)
- `frontend/src/views/Recommendations.tsx` (inline styles → classes), `Ask.tsx`, `Fodder.tsx`, `CardSearch.tsx`, `frontend/src/App.tsx` (token swaps)
- `frontend/package.json` (+3 @fontsource packages)
- `.claude/skills/fcpricemaster/SKILL.md` (new), `README.md` (new)
- `SESSION_LOG.md`, `ROADMAP.md`

### 2026-06-10 — session 35
**Goal:** Data quality overhaul — staleness guard, untradeable detection, full card sweep. Root cause: recommendations were being generated on April price data, and untradeable SBC cards (Son TOTS HM etc.) had their SBC cost estimates stored as market prices.

**Done:**
- **Full data audit (before any changes):** 668/788 tradeable cards >24h stale (499 >7d, 217 >30d); 103,240 of 590k snapshots violated FUT increment rules (= SBC cost estimates, not BINs); June 10 recommendations had price lags up to 1,261h (Bellingham TOTS buy on seed data from April 18); all 1,148 cards were tradeable=1 — untradeable detection had never fired; 0 cards matched `%SBC%`/`%Objective%` version names (FUT.GG uses "TOTS HM" etc., so version-name matching alone is insufficient).
- **Migration 0011 (data_quality):** version-name untradeable rule (0 rows — kept as forward guard); deleted 103,240 invalid-increment snapshots; marked 635 cards untradeable for having no valid BIN in 30 days (1,148 → 513 tradeable at migration time). Delete runs BEFORE the 30-day rule so SBC-estimate-only cards get caught.
- **Migration 0012 (recommendation_metadata):** added `dismissed`, `dismissed_reason` (NOTE: `dismissed_at` already existed from 0006 — prompt assumed it didn't); backfilled flag.
- **Scraper (futgg.py):** `_is_real_bin_price()` (increment ladder + SBC/Objective version regex); `_classify_tradeable()` three-state (0 = untradeable evidence / 1 = verified BIN / None = extinct, no evidence); `_page_is_tradeable()` detail-page check (Prices tab / Price Momentum presence) wired into `fetch_card_prices` — flips tradeable=0 + skips snapshot when absent, restores tradeable=1 when present; `fetch_hot_cards` + `fetch_cards_by_rating` now classify on every extracted price; `_upsert_card` gained restore semantics (tradeable=1 on verified BIN) so the 30-day migration's false positives self-heal.
- **Recommender staleness guard:** `STALE_THRESHOLD_HOURS=24`, `MIN_SNAPSHOTS=3` filtering inside `_get_candidates` (covers scheduler + HTTP trigger paths), platform-scoped. Verified live: skipped Reach (229.9h), Messi (112.4h), Luna (147.9h) — the exact cards behind the bad recs — passed 10 fresh candidates.
- **Data age to LLM:** `_price_context` returns `last_snapshot_ts`/`data_age_hours`/`snapshot_count`; "Last price: X coins (recorded N.Nh ago, M data points)" line in ask.py, recommender.py, AND main.cjs (the Node path the Ask UI actually uses); CRITICAL INSTRUCTION stale-data paragraph added to all three system prompts.
- **Full card sweep:** `fetch_all_cards_paginated()` pages through `/players/?page=N` per band (3-6s jitter, 50-page cap, stops on 2 consecutive empty pages); scheduler jobs `full_card_sweep_pc` 06:00 UTC, `full_card_sweep_console` 06:30 UTC over bands 78-81/82-84/85-87/88-90/91-93/94-99 with per-band scraper_health rows (`futgg_sweep_{min}_{max}_{platform}`). Live test (85-87 PC): found 1,061 / new 901 / snapshots 735 / skipped_untradeable 188 across 36 pages.
- **Dismiss with reason:** Dismiss button → inline dropdown (Wrong price data / Card is untradeable / Already own this / Bad call / Other); stores reason + flag + timestamp; "Wrong price data" also fires `db:requestFreshPrice` → new `POST /fetch-card` endpoint on the scheduler HTTP server → queues immediate `fetch_card_prices` re-scrape (which itself runs the detail-page tradeability check).
- **Tests: 170/170 passing** (required ≥149). 2 datetime naive/aware bugs found by tests and fixed; 2 tests updated to the new intended behavior (mock prices made increment-valid; pool-B 2-snapshot case now correctly blocked by the guard); 19 new parametrized tests for `_is_real_bin_price`/`_classify_tradeable` + 2 staleness-guard tests. `tsc --noEmit` clean, all `.cjs` syntax-checked.
- DB after session: 2,049 cards (sweep test added ~900), 1,286 tradeable, 487,601 snapshots, 0 invalid-increment rows.

**Next:** RESTART THE APP (close Electron, relaunch dev.ps1) — the scheduler running since 03:44 still has pre-session-35 code and kept writing ~5-18 invalid snapshots/hour until I purged them at session end. After restart: tonight's 06:00/06:30 UTC sweeps populate both platforms; then owner visual pass on the dismiss-reason dropdown. Anthropic credits still $0 (recs only work via NVIDIA providers).

**Gotchas:**
- The owner's Electron app + scheduler were live the whole session — every DB number drifted between queries, and the old scheduler re-inserted invalid snapshots AFTER migration 0011 ran (purged twice; final purge at session end). New scraper code only takes effect on restart.
- `recommendations` schema differs from the session prompt's assumptions: columns are `call`/`ts_utc` (not `action`/`recommended_at`) and `dismissed_at` already existed — migration 0012 only adds the two missing columns.
- Naive vs aware datetimes: `datetime.fromisoformat()` on DB timestamps without `Z` returns naive datetimes; subtracting from `datetime.now(timezone.utc)` raises TypeError. Both new parsers normalize naive → UTC.
- EXTINCT cards must NOT be marked untradeable (tradeable cards can be temporarily extinct) — hence the three-state `_classify_tradeable` instead of the binary check the prompt sketched.
- An SBC estimate can randomly land on a valid increment (~1/10 above 100k) and briefly restore tradeable=1 with one snapshot — MIN_SNAPSHOTS=3 plus the detail-page check on re-scrape contain this.
- FUT.GG /players/ pagination serves ~30 cards/page and pages beyond the data return 0 anchors; 85-87 band alone is 36 pages, so a full 6-band sweep takes ~25-40 min/platform at 3-6s jitter.

**Changed files:**
- `backend/src/db/migrations/0011_data_quality.sql` (new)
- `backend/src/db/migrations/0012_recommendation_metadata.sql` (new)
- `backend/src/scrapers/futgg.py` (_is_real_bin_price, _classify_tradeable, _page_is_tradeable, _upsert_card restore semantics, list-page enforcement, fetch_all_cards_paginated + module wrapper, shared _LIST_EXTRACT_JS, SWEEP_BANDS)
- `backend/src/workers/scheduler.py` (job_full_card_sweep + 2 cron jobs, POST /fetch-card endpoint, scraper passed to trigger server)
- `backend/src/llm/recommender.py` (STALE_THRESHOLD_HOURS/MIN_SNAPSHOTS, _passes_staleness_guard in _get_candidates, Last price line, stale-data prompt paragraph)
- `backend/src/llm/context_builder.py` (last_snapshot_ts/data_age_hours/snapshot_count in _price_context)
- `backend/src/llm/ask.py` (Last price line, stale-data prompt paragraph)
- `backend/tests/test_futgg.py` (increment-valid mock prices, 19 new helper tests)
- `backend/tests/test_recommender.py` (guard-aware pool-B tests, 2 new staleness tests)
- `frontend/electron/main.cjs` (db:requestFreshPrice, data age in buildAskContext/formatUserMessage, stale-data prompt paragraph, selftest list)
- `frontend/electron/db-queries.cjs` (card_id + dismissed_reason in rec queries, dismiss with reason)
- `frontend/electron/preload.cjs` (requestFreshPrice)
- `frontend/src/electron.d.ts` (requestFreshPrice, dismiss reason types)
- `frontend/src/views/Recommendations.tsx` (dismiss-reason dropdown, requestFreshPrice on wrong-price, dismissed-reason display)
- `ROADMAP.md`, `ARCHITECTURE.md`, `SESSION_LOG.md` (this entry)

### 2026-06-10 — session 34
**Goal:** Kill orphan workers, fix double-spawn (Session 33 root causes), add NVIDIA timeouts + cold-start UX, handle gpt-oss empty responses.

**Done:**
- Killed all orphaned processes (100+ python/node/electron from 7 launch generations); confirmed port 8765 free afterward.
- **FIX 1 (double-spawn):** main.cjs `app.whenReady` only spawns workers when `AUTO_START_BACKEND !== 'false'`; dev.ps1 sets `AUTO_START_BACKEND=false` before `pnpm dev:electron`. Verified live: after dev.ps1 launch, Electron logged `AUTO_START_BACKEND=false — dev.ps1 owns worker spawning`, exactly 3 logical workers running (6 python PIDs — uv's exec chain doubles each worker's PID count; this also retro-explains Session 33's "2× per generation" counts), exactly ONE owner of port 8765.
- **FIX 1C:** scheduler.py exits via `os._exit(1)` on port-8765 bind failure, after FATAL log + `scraper_health` failure row (source=scheduler). `sys.exit` would only kill the asyncio task.
- **FIX 2A (timeout):** `callNvidiaModel` combines `AbortSignal.timeout(120000)` with the user-cancel signal via `AbortSignal.any` (Node 24.14.0 confirmed). `TimeoutError` → friendly "Model timed out after 120s" error verdict in both `callSingleProvider` and legacy `askMultiModel`.
- **FIX 2B/2C (cold-start UX):** `db:callSingleProvider` NVIDIA branch arms a 15s timer that sends a `provider-status` IPC event (`_e.sender.send`); cleared in finally. preload.cjs exposes `onProviderStatus` (returns unsubscribe fn); electron.d.ts typed; Ask.tsx subscribes (session-gated via `sessionIdRef`), stores hints in `coldStartHints` state, PendingCard renders the italic grey hint under "Querying…". Hints cleared on each new Analyse.
- **FIX 2D:** nvidia_provider.py httpx timeout 60→120s (both text + vision call sites).
- **FIX 3:** `callNvidiaModel` throws a clear error on empty `content` (reasoning budget exhausted); `max_tokens` 1500 for gpt-oss models, 500 otherwise — applied in main.cjs AND nvidia_provider.py (Python side added after watching the live gpt-oss recommender run fail with `non-JSON: {` truncation).
- Verification: `pnpm build` clean; selftest exits 0; **149/149 pytest** (run twice); trigger fired with Node fetch (the same client Electron uses) logged `HTTP trigger: run-recommendations for pc via deepseek-v4-pro` — provider routing through the rebuilt (non-orphan) scheduler confirmed.
- A gpt-oss-120b generation (triggered from the app UI while testing) added 3 recommendations with `model_id='openai/gpt-oss-120b'` in the DB — end-to-end NVIDIA rec generation works.

**Next:** Owner visual pass: (1) Ask with DeepSeek only → cold-start hint should appear after 15s, verdict or clean timeout by 120s; (2) Ask with Kimi only → verdict in ~2-6s; (3) Recommendations → DeepSeek → Generate now → green model badge. Also consider: skip/deprioritize DeepSeek V4 Pro in the recommender — under the long recommender prompt it exceeded even 120s repeatedly tonight.

**Gotchas:**
- `curl.exe`/`Invoke-WebRequest` are unreliable clients for the minimal trigger server (single `read(2048)`, no `Expect: 100-continue` handling) — body can be missed → provider falls back to haiku. Electron's fetch (undici) sends headers+body together and works. Test with Node fetch, not curl.
- Kimi K2.6 on NVIDIA NIM is flaky at temperature=0 with short prompts: 2 of 4 test calls degenerated (`finish_reason=repetition`, "the the the…") — app surfaces these as error cards; a retry usually succeeds.
- httpx `ReadTimeout` stringifies to '' → recommender logs "LLM call failed for X: " with empty reason. Cosmetic, not fixed this session.
- The scheduler running tonight was started before the Python gpt-oss max_tokens fix — that fix takes effect next launch.

**Changed files:**
- `frontend/electron/main.cjs` (AUTO_START_BACKEND gate, NVIDIA timeout + AbortSignal.any, cold-start provider-status event, empty-content error, gpt-oss max_tokens, TimeoutError handling ×2)
- `frontend/electron/preload.cjs` (onProviderStatus)
- `frontend/src/electron.d.ts` (onProviderStatus type)
- `frontend/src/views/Ask.tsx` (coldStartHints state + subscription, PendingCard hint rendering)
- `scripts/dev.ps1` (sets AUTO_START_BACKEND=false for Electron)
- `backend/src/workers/scheduler.py` (fatal exit + scraper_health row on port 8765 bind failure)
- `backend/src/llm/providers/nvidia_provider.py` (timeout 120s ×2, gpt-oss max_tokens 1500)
- `ROADMAP.md`, `ARCHITECTURE.md`, `SESSION_LOG.md` (this entry)

### 2026-06-10 — session 33
**Goal:** Read-only diagnostic audit of the Ask flow ("Querying…" hang on DeepSeek) and Recommendations Generate-now flow. No code changes.

**Done:**
- IPC bridge audit: preload.cjs exposes 27 functions; main.cjs registers 22 `db:` handlers + 5 settings/backend handlers. Perfect 1:1 match — no missing entries, no dead stubs.
- Ask flow traced: `buildAskContext` → N× parallel `callSingleProvider` → `logAskMulti`. The Ask flow never touches Python — Anthropic and NVIDIA calls are raw `fetch()` from the Electron main process (main.cjs `callAnthropic` line 340, `callNvidiaModel` line 303).
- Live NVIDIA API test (exact main.cjs fetch replicated in Node): `kimi-k2.6` answered in 437 ms, `gpt-oss-120b` in 259 ms, but **`deepseek-v4-pro` took 67 s** to return HTTP 200; `qwen3-80b` and `mistral-small-4` did not answer within 30 s. The model IDs are all valid — slow ones are NVIDIA free-tier cold-start/queue latency.
- **ROOT CAUSE 1 (Ask hang):** `callNvidiaModel` has no fetch timeout, so slow NVIDIA models leave the card on "Querying…" for 1–2+ minutes. Not an IPC bug.
- **ROOT CAUSE 2 (Recommendations):** port 8765 is owned by an ORPHAN scheduler (PID 46920, started **2026-05-28**) running 13-day-old code that ignores `provider_id`. Verified live: POST with `provider_id: deepseek-v4-pro` logged as old-format `run-recommendations for pc` (no `via …`). All newer schedulers log `[Errno 10048]` bind failure and keep running without a trigger server.
- **ROOT CAUSE 3 (orphan factory):** every `dev.ps1` launch double-spawns all 3 Python workers — dev.ps1 lines 52–78 AND main.cjs `app.whenReady` (`startBackend`/`startDiscordIngest`/`startTwitterIngest`). Found 14 scheduler, 14 discord, 8 twitter python.exe processes from 7 launch generations (oldest 5/28). Explains every duplicated line in scheduler.log and discord_ingest.log.
- Python backend checks: NVIDIA_API_KEY present (.env, `nvapi-` prefix, len 70), ANTHROPIC_API_KEY present (len 108); all 7 providers registered AND available; httpx timeout in nvidia_provider.py is 60 s — shorter than DeepSeek's observed 67 s cold-start, so the Python rec path would also time out on deepseek.
- Could not click the Electron UI / read DevTools console from this shell; substituted exact-code-path network tests. Manually fired POST /run-recommendations (deepseek-v4-pro): HTTP 200, `recs_added: 0` (handled by the stale orphan).

**Next:** Remediation session: (1) kill all orphaned python.exe workers; (2) make ONE owner spawn the workers (either dev.ps1 or Electron, not both); (3) add a timeout (~120 s) + cold-start messaging to `callNvidiaModel`, and raise the httpx timeout in nvidia_provider.py; (4) consider making scheduler exit or alert loudly when port 8765 bind fails.

**Gotchas:**
- The trigger server has no `/health` endpoint — only `POST /run-recommendations`; a 404 from /health means it IS reachable.
- `gpt-oss-120b` is a reasoning model: with small max_tokens it returns empty `content` (reasoning consumed the budget) → `JSON.parse('')` would throw in `parseVerdictText`. Watch with max_tokens=500.
- Session-32 verification ("all plumbing present and correct") was right about the code, but live behavior differs because the process serving 8765 predates that code.

**Changed files:**
- `SESSION_LOG.md` (this entry — audit only, no code changed)
**Goal:** Fix broken IPC bridge (BUG 1), implement real HTTP-level cancel via AbortController (BUG 2), verify Recommendations Generate now (BUG 3).

**Done:**
- BUG 1 audit: all 4 handlers (`buildAskContext`, `callSingleProvider`, `logAskMulti`, `triggerRecommendations`) were already present in preload.cjs from Session 31. Electron.d.ts and selftest both confirmed. No changes needed.
- BUG 2 — Real cancel implemented end-to-end:
  - `callNvidiaModel` and `callAnthropic` now accept optional `signal` parameter; passed to `fetch()` via `fetchOpts.signal`.
  - `activeSessions` Map added to main.cjs (key: `${session_id}_${provider_id}` → AbortController).
  - `db:callSingleProvider` creates an AbortController per call, stores in Map, passes signal to API fetch, deletes from Map in finally block. AbortError caught and returned as `{error: 'cancelled', action: 'hold', confidence: 0, ...}` so IPC never throws.
  - `db:cancelSession` IPC handler: iterates `activeSessions`, aborts all controllers matching `session_id`, deletes them.
  - `cancelSession` added to preload.cjs and electron.d.ts.
  - Ask.tsx: `sessionIdRef` added; each Analyse click generates `crypto.randomUUID()` stored in ref; `session_id` passed to every `callSingleProvider` call; `handleCancel` fires `window.fcdb.cancelSession` before aborting the local AbortController. If a verdict returns with `error === 'cancelled'`, state is set to `'cancelled'` (grey CancelledCard) not error.
  - Selftest now lists 22 handlers (added `db:cancelSession`).
- BUG 3 — Recommendations Generate now: traced full chain. `triggerRecommendations` in preload → `db:triggerRecommendations` in main.cjs → HTTP POST to 127.0.0.1:8765 → scheduler `_http_trigger_server` → `generate_recommendations(platform, db_path, 3, provider_id)`. All plumbing present and correct from Session 30/31.
- `pnpm build` clean (TypeScript + Vite). 149/149 pytest pass. Selftest exits 0.

**Next:** Owner visual sign-off pass in the Electron app: (1) 3-model Ask query → verdicts appear incrementally, timing shown; (2) Click Cancel mid-query → providers show grey "cancelled" badges, HTTP fetches actually aborted (no cost for NVIDIA); (3) Recommendations → DeepSeek V4 Pro → Generate now → green model badge on result card.

**Gotchas:**
- AbortSignal is passed as a fetchOpts property, not a spread, to avoid issues with older Electron Node ABI that don't support `{ ...opts, signal }` on Request objects.
- If a callSingleProvider call completes *just before* cancelSession is called, the session key is already deleted from activeSessions (via finally) — cancelSession will just find nothing to abort. This is a benign race; the result is already on its way back to the renderer, but `ctrl.signal.aborted` in Ask.tsx will suppress updating the display.
- `error === 'cancelled'` check in Ask.tsx applies only when the verdict arrives after the AbortController was already aborted (race condition). The main fast path is the AbortError thrown from fetch before a response arrives.
- Reddit is at 172+ consecutive failures (403 from old.reddit.com without credentials). Set `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` in `.env` to re-enable.

**Changed files:**
- `SESSION_LOG.md`
- `ROADMAP.md`
- `frontend/electron/main.cjs`
- `frontend/electron/preload.cjs`
- `frontend/src/electron.d.ts`
- `frontend/src/views/Ask.tsx`

---

### 2026-06-10 — session 31
**Goal:** Ask UI polish (render error, cancel button, timing, history, Select all/Clear all, Mistral Vision gating); Recommendations multi-model badge + budget bar; migration 0010 for model_id.

**Done:**
- Fixed BUG 1: all `window.fcdb.*` calls in Ask.tsx now null-guarded via `isElectron` flag. Dev mode (browser without Electron) shows all providers as available with a yellow "dev mode" badge; no render errors.
- Refactored Ask IPC into three handlers: `db:buildAskContext` (context only), `db:callSingleProvider` (one model + timing), `db:logAskMulti` (aggregate history row). Ask.tsx fires all providers in parallel; each verdict card appears as it resolves (incremental rendering).
- Added AbortController cancel: Cancel button replaces Analyse during loading; pending providers switch to "cancelled" cards immediately; resolved results are preserved.
- Per-model timing: `elapsed_ms` returned from `db:callSingleProvider`; shown as "X.Xs" in each verdict card.
- Verdict cards redesigned: responsive 2-col grid, colored left border (NVIDIA green / Anthropic purple), confidence % in large text, timing + cost meta, "Show more" toggle at 120 chars, error cards with red border.
- Select all / Clear all toggles above provider row. Select all includes text models only (not vision). Clear all + submit shows "Select at least one model" inline error.
- Mistral Vision checkbox is always disabled unless an image is attached (cannot accidentally fire it on text-only queries). Auto-checks + note "Auto-selected for image analysis" on image attach. Unchecks + re-disables on Remove. Image button label shows filename once attached.
- History fixed: every multi-model session logs one `ask_multi` row to `llm_calls`; `getLLMHistory` filters to `feature IN ('ask', 'ask_multi')`; History count shows number of `ask_multi` sessions; clicking a history row expands to show full verdict cards; verdict summary badges (N× BUY/HOLD/AVOID) shown inline.
- Migration 0010: `recommendations.model_id TEXT DEFAULT 'claude-haiku-4-5-20251001'`. Applied to live DB.
- `_insert_recommendation` in recommender.py accepts + stores `model_id`. `generate_recommendations` passes `call_model`; `_fodder_recommendations` same; `_futties_85_recommendation` uses `'structural'`.
- Recommendation cards show colored model badge: green for NVIDIA models, purple for Claude Haiku, grey for Structural.
- Recommendations budget bar shows "Model: [name] · Free (NVIDIA · 40 RPM)" when a free provider is selected.
- Added `getModelDisplay()` helper in Recommendations.tsx mapping full model IDs to short labels + provider type.
- Multi-verdict CSS added to App.css (was entirely missing): grid, card, header, confidence, reasoning, expand, footer, provider badges, disagree banner, toggle links.
- All 21 IPC handlers registered in selftest. 149/149 tests pass. `pnpm build` clean. Classifier smoke test: 4/4 correct.

**Next:** Visual sign-off pass by owner in the Electron app: Ask tab (image attach → vision auto-check, 3-model query, timing, cancel, history count), Recommendations tab (DeepSeek V4 Pro → Free budget bar + badge on generated card).

**Gotchas:**
- `db:callSingleProvider` for Haiku logs to `llm_calls` with `feature='ask'` (budget tracking). `db:logAskMulti` logs with `feature='ask_multi'` and `cost_usd=0` — no double-counting.
- AbortController only signals the renderer; IPC calls in main.cjs still complete. Models that respond after cancel are silently discarded. For NVIDIA (free) this wastes nothing; a stray Haiku call will still complete and log its cost.
- `model_id` in recommendations is the raw model string from the provider (e.g. `'deepseek-ai/deepseek-v4-pro'`); `getModelDisplay()` maps these to human-readable labels.
- Old recommendations rows have `model_id = 'claude-haiku-4-5-20251001'` (the migration DEFAULT).

**Changed files:**
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `SESSION_LOG.md`
- `backend/src/db/migrations/0010_rec_model_id.sql` (new)
- `backend/src/llm/recommender.py`
- `frontend/electron/main.cjs`
- `frontend/electron/preload.cjs`
- `frontend/electron/db-queries.cjs`
- `frontend/src/lib/types.ts`
- `frontend/src/electron.d.ts`
- `frontend/src/views/Ask.tsx`
- `frontend/src/views/Recommendations.tsx`
- `frontend/src/App.css`

---

