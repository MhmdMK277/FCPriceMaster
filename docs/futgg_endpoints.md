# FUT.GG Endpoint Investigation & Scraping Strategy
**Investigated:** 2026-04-19 | **Updated:** 2026-04-19 (session 4)

---

## Decision

**Playwright DOM scraping of public pages. No /api/* calls.**

`robots.txt` disallows `/api/*`. The site is a Cloudflare-protected SPA.
We navigate public URLs as a real browser user, wait for JS to render, and
read prices from the DOM. The browser makes internal XHR calls to their API —
we only read what is displayed on screen.

Rejected alternatives:
- Direct `httpx` to `/api/*` — violates robots.txt, blocked by Cloudflare
- XHR interception via Playwright — still hits `/api/*` directly
- cloudscraper / cf-clearance / FlareSolverr — adversarial; explicitly out of scope

---

## robots.txt

```
User-agent: *
Disallow: /api/*   ← entire API off-limits
Disallow: /accounts/*
Disallow: /auth/*
...
```

---

## Public pages used for scraping

### 1. Trending hot-card list

```
URL: https://www.fut.gg/players/trending/
```

Renders up to ~30 player cards by default. Platform is switched via a Radix
UI dropdown triggered by the `[title="Select platform"]` button in the top nav.
The dropdown has `[role="menuitem"]` entries: "Console" and "PC".

**Card DOM structure** (each card is an `<a>` anchor):
```html
<a href="/players/188350-marco-reus/26-67297214/" class="group/player ...">
  <div class="fc-card-container ...">
    <div class="fc-card ...">
      <img alt="Reus - 93 - TOTS HM" src="...">   ← name / rating / version
      <div class="... font-din ...">               ← price badge
        <div ...>
          <div class="bg-orange"><span>CAM</span></div>  ← position
          <span class="font-numbers-bold">93.0</span>    ← rating
        </div>
        <div ...>
          <img alt="TOTS HM">   ← version icon
          355.6K                ← BIN price (K/M notation)
        </div>
      </div>
    </div>
  </div>
</a>
```

**Selector used:** `a[href*="/players/"][href*="/26-"]`

**Card key extraction:** from href `/players/188350-marco-reus/26-67297214/`
→ `card_key = "26-67297214"` (edition-cardId)

**Price parsing:**
| DOM text | Parsed value |
|----------|-------------|
| `355.6K` | 355600 |
| `1.2M` | 1200000 |
| `355,150` | 355150 |
| `EXTINCT` | None |
| `N/A` | None |

### 2. Card detail page

```
URL: https://www.fut.gg/players/{player_id}-{slug}/{edition}-{card_id}/
Example: https://www.fut.gg/players/188350-marco-reus/26-67297214/
```

Main card price badge is in the first `.fc-card-container .font-din` element.
Badge text for the main card: just `"355,550"` (no position/rating prefix).

Platform is switched the same way as on the list page.

---

## Platform switching

1. Remove CMP consent overlay: `document.querySelectorAll('#cmpwrapper, .cmpwrapper').forEach(el => el.remove())`
2. Click `[title="Select platform"]` with `force=True` (CMP may still intercept)
3. Wait ~600ms for Radix dropdown to open
4. Click `[role="menuitem"]:has-text("PC")` or `...("Console")`
5. Wait ~1500ms for page to re-render with new platform's prices

Platform preference is NOT stored in localStorage (only CMP consent is).
It appears to be stored in a React/Zustand context in memory.

---

## Sample prices observed (2026-04-19)

| Card | Console | PC |
|------|---------|-----|
| Reus TOTS HM 93 | 360,100 | 383,300 |
| Lobotka TOTS HM 90 | EXTINCT | EXTINCT |
| Jacobo Ramón TOTS 93 CB | 124,000 | 99,500 |
| Maignan TOTS 92 GK | 47,300 | 40,500 |
| Son TOTS HM 93 LM | 537,900 | 581,400 |

Prices differ meaningfully across platforms as expected.

---

## Rate limiting

No explicit `Retry-After` headers observed. We apply 5–10s jitter between
page loads. One full 500-card sweep takes roughly 40–60 minutes.
Single shared browser context (looks like one long user session to the site).

---

## Known limitations

- Trending page shows ~30 cards per load. For 500-card coverage, pagination
  or additional public list pages (`/players/in-packs/`, `/players/momentum/`)
  will be needed. This is a Phase 1.3 extension task.
- Card detail pages require knowing the full player slug in the URL.
  The scraper resolves slugs via a search page (`?search={card_id_str}`) as
  a fallback. This makes `fetch_card_prices` slower than `fetch_hot_cards`.
- Player names with non-ASCII characters display with encoding artifacts in
  the terminal on Windows (e.g. "Jacobo Ramón" → "Jacobo Ram?n"). The DB
  stores the correct bytes; this is a terminal display issue only.
