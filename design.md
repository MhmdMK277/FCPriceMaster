# Design — FCPriceMaster

A locked design system for this app. Every view redesign reads this file before
emitting code. Do not regenerate per view — extend or amend this file when the
system needs to grow.

## Genre
atmospheric — dark canvas, warm glow, data-dense trading tool used after dark.

## Macrostructure family
- App views (all eight): **Workbench** — side-rail nav, dense data surfaces,
  typographic hierarchy carries the page. No heroes, no marketing sections,
  no enrichment beyond the canvas blooms.

## Theme — "Coin" (custom, gold-anchored)
FUT is a gold-coin economy; the accent is coin gold. Green/red are *semantic
only* (price rise / fall) and never decorative.

- `--color-paper`      oklch(16% 0.014 75)  — warm near-black canvas
- `--color-paper-2`    oklch(20% 0.016 75)  — raised surface (cards, inputs)
- `--color-paper-3`    oklch(24% 0.018 75)  — highest (hover, active surfaces)
- `--color-ink`        oklch(94% 0.012 85)  — warm off-white
- `--color-ink-2`      oklch(74% 0.02 80)   — muted text
- `--color-faint`      oklch(56% 0.018 78)  — faint text, timestamps
- `--color-rule`       oklch(27% 0.014 75)  — hairline (tables only)
- `--color-accent`     oklch(82% 0.14 85)   — coin gold
- `--color-accent-dim` oklch(70% 0.12 82)
- `--color-accent-ink` oklch(22% 0.06 85)   — text on gold
- `--color-focus`      = accent
- `--color-rise`       oklch(76% 0.15 155)  — price up / buy
- `--color-rise-bg`    oklch(30% 0.05 155)
- `--color-fall`       oklch(66% 0.19 25)   — price down / avoid
- `--color-fall-bg`    oklch(26% 0.06 25)
- `--color-warn`       oklch(78% 0.14 60)   — degraded / warning
- `--color-warn-bg`    oklch(28% 0.05 60)

## Typography (2+1 rule)
- Display: **Bricolage Grotesque**, weights 600–800, tracking −0.02em.
  Used for: wordmark, view titles, big verdict/confidence numerals.
- Body: **Geist**, 400/500. All UI text and prose.
- Outlier (data register): **Geist Mono**, 400/500. One role: numeric data —
  prices, percentages, counts, timestamps. `tabular-nums` everywhere it appears.
- Fonts are self-hosted via @fontsource (CSP is default-src 'self'; the app
  must work offline).
- Type scale anchor: UI base 14px; view titles `--text-xl` (22px);
  display numerals `--text-2xl` (28px). Data-dense desktop tool — no
  marketing display sizes.

## Spacing
4-point named scale in `tokens.css` (`--space-*`). Views use named tokens only.

## Elevation
Dark-surface discipline: elevation by *lightness* (paper → paper-2 → paper-3),
never glow shadows. Hairline `--color-rule` borders only on table rows and
menus. Cards are borderless raised surfaces.

## Canvas
Two fixed radial gold blooms on `body` (≤4% alpha, ~25% footprint each,
top-left and bottom-right). No animation. Data surfaces sit on top, opaque.

## Motion
- Easings: `--ease-out: cubic-bezier(0.16, 1, 0.3, 1)` only.
- Durations: `--dur-short: 120ms`, `--dur-med: 220ms`.
- Reveal: one fade+2px-rise on view mount (180ms). Nothing animates on scroll.
- Transitions name their properties — `transition: all` is banned.
- Focus rings appear instantly, never transitioned.
- Reduced-motion: all motion collapses to ≤150ms opacity.

## Microinteractions stance
- Silent success; toasts only for async results and failures.
- Buttons: 1px translate on :active. One hover signal per element.
- Hover affordances always have a focus-visible equivalent.

## CTA voice
- Primary: gold fill (`--color-accent` / `--color-accent-ink`), radius 8px,
  weight 600, label ≤ 2 words.
- Secondary/quiet: paper-2 fill, ink-2 text, same radius.
- Danger: fall-red fill, used only for destructive actions.

## What views MUST share
- The wordmark (Bricolage 800, gold coin dot).
- Gold accent placement: active nav, focus rings, primary buttons, key
  numerals. ≤5% of any viewport.
- The three faces and their roles. Mono = data, never labels.
- Status language: dot + label (never coloured left-edge stripes).

## What views MAY differ on
- Data layout (table vs card list vs grid) per the content's shape.
- Density (Signals is a feed; Top Movers is a table; Ask is a form).

## Per-view allowances
- No enrichment in any view. The canvas blooms are the only atmosphere.
- Emoji are banned as UI icons. Status is typographic.

## Exports

### tokens.css
See `frontend/src/tokens.css` — the canonical token file for this app.
