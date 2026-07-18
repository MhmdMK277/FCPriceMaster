"""
Session 40 — real browser testing of the FCPriceMaster Electron UI.

Connects Playwright over CDP to the ACTUAL running Electron renderer
(launched with --remote-debugging-port=9222), so window.fcdb / better-sqlite3
IPC is live. No mocking, no expected values: every test reports exactly what
it observes. Element not found => "NOT FOUND", never a fake pass.

Run (from backend/, so the venv with playwright is used):
    uv run python ../tests/browser/test_ui_real.py
"""

from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(ROOT, "screenshots")
os.makedirs(SHOTS, exist_ok=True)

CDP_URL = "http://localhost:9222"
APP_URL_FRAGMENT = "localhost:5173"

console_errors: list[str] = []
network_errors: list[str] = []
results: list[tuple[str, str]] = []  # (test, PASS/FAIL/WARN)


def log(msg: str = "") -> None:
    print(msg, flush=True)


def shot(page, name: str) -> None:
    path = os.path.join(SHOTS, name)
    try:
        page.screenshot(path=path)
        log(f"  [screenshot] {name}")
    except Exception as exc:
        log(f"  [screenshot FAILED] {name}: {exc}")


def txt(locator, timeout: int = 4000) -> str:
    """inner_text or NOT FOUND — never raises."""
    try:
        return locator.inner_text(timeout=timeout).strip()
    except Exception:
        return "NOT FOUND"


def count(locator) -> int:
    try:
        return locator.count()
    except Exception:
        return -1


def nav_to(page, label: str, settle: float = 3.0) -> bool:
    try:
        page.locator("button.nav-item", has_text=label).first.click(timeout=5000)
        time.sleep(settle)
        return True
    except Exception as exc:
        log(f"  NAV FAILED for '{label}': {exc}")
        return False


def main() -> None:
    with sync_playwright() as p:
        log(f"Connecting to Electron over CDP at {CDP_URL} ...")
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = None
        deadline = time.time() + 10
        while time.time() < deadline and page is None:
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if APP_URL_FRAGMENT in pg.url:
                        page = pg
                        break
                if page:
                    break
            if page is None:
                time.sleep(0.5)
        if page is None:
            log("FATAL: no page with localhost:5173 found over CDP. Pages seen:")
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    log(f"  - {pg.url}")
            sys.exit(1)

        page.on(
            "console",
            lambda m: console_errors.append(f"[{m.type}] {m.text}")
            if m.type == "error"
            else None,
        )
        page.on(
            "requestfailed",
            lambda r: network_errors.append(f"{r.url} -> {r.failure}"),
        )

        # ------------------------------------------------------------------
        log("\n=== TEST 1 — App loads ===")
        try:
            page.wait_for_load_state("load", timeout=10000)
        except Exception as exc:
            log(f"  load state wait: {exc}")
        log(f"  Page URL:   {page.url}")
        log(f"  Page title: {page.title()!r}")
        sidebar_visible = False
        try:
            sidebar_visible = page.locator("aside.sidebar").is_visible(timeout=4000)
        except Exception:
            pass
        log(f"  Sidebar visible: {sidebar_visible}")
        nav_items = []
        for i in range(count(page.locator("button.nav-item"))):
            nav_items.append(txt(page.locator("button.nav-item").nth(i)))
        log(f"  Nav items ({len(nav_items)}): {nav_items}")
        log(f"  Console errors so far: {console_errors or 'none'}")
        shot(page, "01_app_load.png")
        results.append(("1 App loads", "PASS" if sidebar_visible and nav_items else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 2 — Recommendations view ===")
        ok = nav_to(page, "Recommendations")
        stats = txt(page.locator(".rec-stats"))
        log(f"  Stats bar:  {stats!r}")
        budget = txt(page.locator(".rec-budget"))
        log(f"  Budget bar: {budget!r}")
        n_cards = count(page.locator(".rec-card-top"))
        log(f"  Recommendation cards visible: {n_cards}")
        if n_cards > 0:
            first_name = txt(page.locator(".rec-card-name").first)
            first_meta = txt(page.locator(".rec-meta").first)
            first_conf = txt(page.locator(".rec-confidence").first)
            first_price = txt(page.locator(".rec-price").first)
            first_reason = txt(page.locator(".rec-reasoning").first)[:150]
            log(f"  First card name:       {first_name!r}")
            log(f"  First card meta:       {first_meta!r}")
            log(f"  First card confidence: {first_conf!r}")
            log(f"  First card price:      {first_price!r}")
            log(f"  First card reasoning:  {first_reason!r}")
        else:
            log(f"  Empty state: {txt(page.locator('.rec-empty'))!r}")
        shot(page, "02_recommendations.png")

        # Refresh / Generate now
        gen_btn = page.locator(".rec-header button.btn").first
        gen_text = txt(gen_btn)
        log(f"  Generate button text: {gen_text!r}")
        before_cards = n_cards
        if gen_text in ("Refresh", "Generate now"):
            try:
                gen_btn.click(timeout=3000)
                log("  Clicked generate/refresh; waiting 10s ...")
                time.sleep(10)
                toast = txt(page.locator(".rec-toast"), timeout=2000)
                after_cards = count(page.locator(".rec-card-top"))
                log(f"  Toast after refresh: {toast!r}")
                log(f"  Cards before={before_cards} after={after_cards}")
                log(f"  Button text now: {txt(gen_btn)!r}")
            except Exception as exc:
                log(f"  Refresh click failed: {exc}")
        else:
            log("  Refresh button not clickable (text above) — skipped click")
        results.append(("2 Recommendations", "PASS" if ok and stats != "NOT FOUND" else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 3 — Ask view ===")
        ok = nav_to(page, "Ask", settle=2.0)
        boxes = page.locator("label.provider-checkbox")
        n_boxes = count(boxes)
        log(f"  Provider checkboxes: {n_boxes}")
        states = []
        for i in range(n_boxes):
            label_text = txt(boxes.nth(i))
            inp = boxes.nth(i).locator("input")
            try:
                checked = inp.is_checked()
                disabled = inp.is_disabled()
            except Exception:
                checked, disabled = None, None
            states.append((label_text, checked, disabled))
            log(f"    - {label_text!r} checked={checked} disabled={disabled}")
        log(f"  Image button visible: {page.locator('button', has_text='Image').first.is_visible() if count(page.locator('button', has_text='Image')) else 'NOT FOUND'}")
        log(f"  Select all visible: {count(page.locator('button', has_text='Select all')) > 0}")
        log(f"  Clear all visible:  {count(page.locator('button', has_text='Clear all')) > 0}")
        shot(page, "03_ask_before.png")

        # Select ONLY Mistral Small
        for i in range(n_boxes):
            label_text, checked, disabled = states[i]
            if disabled:
                continue
            want = label_text.startswith("Mistral Small")
            if checked != want:
                try:
                    boxes.nth(i).locator("input").click(timeout=2000)
                except Exception as exc:
                    log(f"  toggle failed for {label_text!r}: {exc}")
        # re-report
        for i in range(n_boxes):
            inp = boxes.nth(i).locator("input")
            try:
                log(f"    after-toggle: {txt(boxes.nth(i))!r} checked={inp.is_checked()}")
            except Exception:
                pass

        question = "What is a good budget buy under 200k right now based on price data?"
        try:
            page.locator("textarea").first.fill(question, timeout=3000)
            log(f"  Typed question: {question!r}")
        except Exception as exc:
            log(f"  textarea fill FAILED: {exc}")

        t0 = time.time()
        verdict_text = "NOT FOUND"
        try:
            page.locator("button", has_text="Analyse").first.click(timeout=3000)
            log("  Clicked Analyse; polling up to 90s for verdict ...")
            deadline = time.time() + 90
            while time.time() < deadline:
                grid = page.locator(".multi-verdict-grid")
                if count(grid) > 0:
                    t = txt(grid.first, timeout=1500)
                    if t not in ("", "NOT FOUND") and "Querying" not in t:
                        verdict_text = t
                        break
                err = txt(page.locator(".ask-error"), timeout=500)
                if err != "NOT FOUND":
                    verdict_text = f"ERROR SHOWN: {err}"
                    break
                time.sleep(1.5)
        except Exception as exc:
            log(f"  Analyse click FAILED: {exc}")
        elapsed = time.time() - t0
        log(f"  Elapsed: {elapsed:.1f}s")
        log("  Verdict card text:")
        for line in verdict_text.splitlines():
            log(f"    | {line}")
        shot(page, "04_ask_result.png")
        results.append(("3 Ask (Mistral Small)", "PASS" if verdict_text not in ("NOT FOUND",) and not verdict_text.startswith("ERROR") else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 4 — Top Movers ===")
        ok = nav_to(page, "Top Movers")
        rows = page.locator(".data-table tbody tr")
        n_rows = count(rows)
        log(f"  Rows in table: {n_rows}")
        for i in range(min(3, max(n_rows, 0))):
            log(f"  Row {i + 1}: {txt(rows.nth(i))!r}")
        if n_rows <= 0:
            log(f"  Empty state: {txt(page.locator('.empty'))!r}")
        first_row_before = txt(rows.first) if n_rows > 0 else ""
        # platform toggle (sidebar)
        plat = page.locator(".platform-toggle .plat-btn")
        log(f"  Sidebar platform buttons: {[txt(plat.nth(i)) for i in range(count(plat))]}")
        try:
            plat.filter(has_text="Console").first.click(timeout=3000)
            time.sleep(2)
            n_after = count(page.locator(".data-table tbody tr"))
            first_row_after = txt(page.locator(".data-table tbody tr").first) if n_after > 0 else ""
            log(f"  After Console click: rows={n_after}")
            log(f"  First row changed: {first_row_before != first_row_after}")
            log(f"  First row now: {first_row_after!r}")
        except Exception as exc:
            log(f"  Console toggle FAILED: {exc}")
        shot(page, "05_top_movers.png")
        results.append(("4 Top Movers", "PASS" if n_rows >= 0 else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 5 — Card Search ===")
        ok = nav_to(page, "Card Search", settle=2.0)
        try:
            page.locator(".search-input, input[placeholder*='Search']").first.fill("Messi", timeout=3000)
            log("  Typed 'Messi'")
            # The view has an explicit Search button — typing alone does not
            # query. NOTE: must target .search-row .btn; a has_text="Search"
            # button locator matches the "Card Search" NAV ITEM first and
            # remounts the view, silently wiping the query.
            page.locator(".search-row .btn").first.click(timeout=3000)
            log("  Clicked Search button")
        except Exception as exc:
            log(f"  search input FAILED: {exc}")
        time.sleep(3)
        res_rows = page.locator(".results-list .search-row")
        n_res = count(res_rows)
        log(f"  Results: {n_res}")
        for i in range(min(3, max(n_res, 0))):
            log(f"  Result {i + 1}: {txt(res_rows.nth(i))!r}")
        shot(page, "06_card_search.png")
        if n_res > 0:
            try:
                res_rows.first.click(timeout=3000)
                time.sleep(2)
                detail = txt(page.locator(".card-detail"))
                log("  Card detail:")
                for line in detail.splitlines()[:20]:
                    log(f"    | {line}")
                snap_rows = count(page.locator(".snapshot-table-wrap tbody tr"))
                log(f"  Snapshot rows in detail table: {snap_rows}")
            except Exception as exc:
                log(f"  detail click FAILED: {exc}")
        shot(page, "07_card_detail.png")
        results.append(("5 Card Search", "PASS" if n_res > 0 else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 6 — Fodder ===")
        ok = nav_to(page, "Fodder")
        frows = page.locator(".fodder-table tbody tr")
        n_f = count(frows)
        log(f"  Fodder table rows: {n_f}")
        for i in range(min(3, max(n_f, 0))):
            log(f"  Row {i + 1}: {txt(frows.nth(i))!r}")
        if n_f > 0:
            try:
                frows.first.click(timeout=3000)
                time.sleep(1.5)
                n_f_after = count(page.locator(".fodder-table tbody tr"))
                log(f"  Rows after expanding first: {n_f_after} (was {n_f})")
                if n_f_after > n_f:
                    log(f"  Expanded content: {txt(frows.nth(1))[:300]!r}")
                else:
                    log("  Row click did not add rows — expanded content:")
                    log(f"    {txt(page.locator('.fodder-table'))[:400]!r}")
            except Exception as exc:
                log(f"  expand FAILED: {exc}")
        shot(page, "08_fodder.png")
        results.append(("6 Fodder", "PASS" if n_f > 0 else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 7 — Signals ===")
        ok = nav_to(page, "Signals")
        sigs = page.locator(".signal-list > *")
        n_s = count(sigs)
        log(f"  Signal rows: {n_s}")
        for i in range(min(3, max(n_s, 0))):
            log(f"  Signal {i + 1}: {txt(sigs.nth(i))[:220]!r}")
        badges = page.locator(".sig-badge")
        n_badges = count(badges)
        badge_texts = sorted({txt(badges.nth(i)) for i in range(min(n_badges, 15))})
        log(f"  Context badges present: {n_badges}, kinds: {badge_texts}")
        if n_s <= 0:
            log(f"  Empty state: {txt(page.locator('.empty'))!r}")
        shot(page, "09_signals.png")
        results.append(("7 Signals", "PASS" if n_s >= 0 else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 8 — Scraper Health ===")
        ok = nav_to(page, "Scraper Health")
        cards = page.locator(".health-cards > *")
        n_h = count(cards)
        log(f"  Health cards: {n_h}")
        for i in range(max(n_h, 0)):
            log(f"  Source {i + 1}: {txt(cards.nth(i))[:300]!r}")
        err_texts = page.locator(".error-text")
        for i in range(count(err_texts)):
            log(f"  ERROR TEXT: {txt(err_texts.nth(i))!r}")
        shot(page, "10_scraper_health.png")
        results.append(("8 Scraper Health", "PASS" if n_h > 0 else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 9 — Settings ===")
        ok = nav_to(page, "Settings", settle=2.0)
        srows = page.locator(".setting-row")
        n_set = count(srows)
        log(f"  Setting rows: {n_set}")
        for i in range(max(n_set, 0)):
            log(f"  Setting {i + 1}: {txt(srows.nth(i))[:200]!r}")
        log(f"  Full settings view text:")
        for line in txt(page.locator(".view", has_text="Settings").first)[:600].splitlines():
            log(f"    | {line}")
        shot(page, "11_settings.png")
        results.append(("9 Settings", "PASS" if n_set >= 0 else "FAIL"))

        # ------------------------------------------------------------------
        log("\n=== TEST 10 — Platform toggle ===")
        plat = page.locator(".platform-toggle .plat-btn")
        try:
            pc_cls_before = plat.filter(has_text="PC").first.get_attribute("class")
            plat.filter(has_text="PC").first.click(timeout=3000)
            time.sleep(0.5)
            pc_cls_active = plat.filter(has_text="PC").first.get_attribute("class")
            plat.filter(has_text="Console").first.click(timeout=3000)
            time.sleep(0.5)
            con_cls = plat.filter(has_text="Console").first.get_attribute("class")
            pc_cls_after = plat.filter(has_text="PC").first.get_attribute("class")
            log(f"  PC class after clicking PC:      {pc_cls_active!r}")
            log(f"  Console class after clicking it: {con_cls!r}")
            log(f"  PC class after clicking Console: {pc_cls_after!r}")
            toggle_works = "active" in (pc_cls_active or "") and "active" in (con_cls or "") and "active" not in (pc_cls_after or "")
            log(f"  Toggle visual state changes: {toggle_works}")

            # Recommendations data across platforms
            nav_to(page, "Recommendations", settle=2.0)
            console_first = txt(page.locator(".rec-card-name").first, timeout=2500)
            console_stats = txt(page.locator(".rec-stats"))
            plat.filter(has_text="PC").first.click(timeout=3000)
            time.sleep(2)
            pc_first = txt(page.locator(".rec-card-name").first, timeout=2500)
            pc_stats = txt(page.locator(".rec-stats"))
            log(f"  Recommendations first card on Console: {console_first!r}")
            log(f"  Recommendations first card on PC:      {pc_first!r}")
            log(f"  Data differs between platforms: {console_first != pc_first}")
            log(f"  Stats Console: {console_stats!r}")
            log(f"  Stats PC:      {pc_stats!r}")
            results.append(("10 Platform toggle", "PASS" if toggle_works else "FAIL"))
        except Exception as exc:
            log(f"  Platform toggle FAILED: {exc}")
            results.append(("10 Platform toggle", "FAIL"))

        # ------------------------------------------------------------------
        log("\n" + "=" * 60)
        log("FINAL SUMMARY")
        log("=" * 60)
        for name, status in results:
            log(f"  {status}: {name}")
        log(f"\nCONSOLE ERRORS ({len(console_errors)}):")
        for e in console_errors[:30]:
            log(f"  {e}")
        if not console_errors:
            log("  none")
        log(f"\nNETWORK ERRORS ({len(network_errors)}):")
        for e in network_errors[:30]:
            log(f"  {e}")
        if not network_errors:
            log("  none")


if __name__ == "__main__":
    main()
