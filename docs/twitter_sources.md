# Twitter/X Ingestion Sources

Last verified: 2026-04-24

## Monitored accounts

| Handle | Category | Priority | Notes |
|--------|----------|----------|-------|
| @FutSheriff | leaks | high | Card reveals, upcoming promos, leaked content |
| @FUT_Scoreboard | content_updates | high | SBC drops, source code changes, live content |
| @FUTDonkey | leaks | medium | Leaks, market commentary |
| @EasijdeFC | — | — | Unverified — add to config/twitter_accounts.yaml when confirmed |
| @ABORTSEFIFA | — | — | Unverified — add to config/twitter_accounts.yaml when confirmed |

Add/remove accounts by editing `config/twitter_accounts.yaml` and restarting the worker.

## Cookie refresh procedure

Twitter session cookies expire after ~30 days of inactivity (longer if the account is actively used).

1. Open Chrome/Edge and navigate to `https://x.com`
2. Log in to the throwaway account (the one that follows the leaker accounts)
3. Open DevTools → Application → Cookies → `https://x.com`
   - Confirm `auth_token` and `ct0` are present
4. Export cookies as Netscape format using a browser extension (e.g. "Get cookies.txt LOCALLY")
5. Save the exported file to `data/.cookies/x_cookies.txt` (overwrite the old one)
6. Restart the Twitter ingest worker:
   - Via dev.ps1: restart dev.ps1
   - Standalone: `uv run python -m src.workers.twitter_ingest` (from `backend/`)

**Signs cookies are expired:** Worker logs `ERROR: cookies_expired` and stops polling.
Scraper Health view will show the twitter source with consecutive failures.

## Polling strategy

- **Approach:** Navigate to `https://x.com/home` (Following timeline) once per cycle
- **Interval:** 50 seconds
- **Why /home not per-profile:** One page load covers all followed accounts simultaneously.
  Per-profile navigation would require 5-6 loads per cycle at higher detection latency.
- **Latency guarantee:** <60 seconds from tweet posting to DB insertion (assuming /home refreshes promptly)

## Known DOM selectors (verified 2026-04-24)

| Data | Selector |
|------|----------|
| Tweet container | `article[data-testid="tweet"]` |
| Author + handle | `[data-testid="User-Name"]` (inner text: "Display Name\n@handle") |
| Tweet text | `[data-testid="tweetText"]` |
| Timestamp | `time[datetime]` (ISO 8601 attribute) |
| Status link | `a[href*="/status/"]` (tweet ID in URL) |
| Media images | `img[src*="pbs.twimg.com/media"]` |

**DOM change detection:** If `article[data-testid="tweet"]` returns zero results on a successfully loaded page,
the worker logs a schema-guard WARNING and increments `consecutive_empty`. After 5 consecutive empty polls,
it logs ERROR and writes a failure row to `scraper_health`. It does NOT stop — the selector may be temporarily
absent (e.g. rate limit soft-block). To fix a real DOM change, update the selectors in `twitter_ingest.py:_extract_tweets`.

## Login page detection

The worker checks `page.url` after navigation:
- If the URL contains `login` or `i/flow` → cookies expired, worker stops
- If page body contains "429" or "Rate limit" → backs off 5 minutes, then resumes

## Rate limit handling

On rate limit detection: worker backs off `_RATE_LIMIT_BACKOFF` seconds (default 300s = 5 minutes).
After backoff, resumes normal 50s polling. Rate limit handling is transparent — no restart needed.
