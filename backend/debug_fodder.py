"""Temporary debug script — delete after use."""
import asyncio

JS = """
(() => {
  var heads = document.querySelectorAll('h2, h3');
  for (var i = 0; i < heads.length; i++) {
    var h = heads[i];
    if (!h.textContent.match(/Cheapest 90 Rated/)) continue;
    var gp = h.parentElement && h.parentElement.parentElement;
    if (!gp) continue;
    var anchors = gp.querySelectorAll('a[href*="/26-"]');
    var cards = [];
    for (var j = 0; j < anchors.length; j++) {
      var a = anchors[j];
      var parts = a.innerText.split('\\n').map(function(s){return s.trim();}).filter(Boolean);
      cards.push({href: a.getAttribute('href') || '', parts: parts.slice(0, 5)});
    }
    return {count: anchors.length, cards: cards};
  }
  return null;
})()
"""

async def debug():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto('https://www.fut.gg/cheapest-by-rating/?platform=pc', wait_until='domcontentloaded', timeout=60000)
        try:
            await page.wait_for_selector('h2', timeout=30000)
        except Exception:
            pass
        try:
            await page.wait_for_selector('a[href*="/26-"]', timeout=30000)
        except Exception:
            pass
        result = await page.evaluate(JS)
        if result:
            print(f"Rating 90: {result['count']} cards")
            for c in result['cards']:
                safe = [p.encode('ascii','replace').decode() for p in c['parts'][:4]]
                print(f"  href={c['href']}, parts={safe}")
        await browser.close()

asyncio.run(debug())
