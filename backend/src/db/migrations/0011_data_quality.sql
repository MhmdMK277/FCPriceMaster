-- Session 35: data quality cleanup.
-- Untradeable (SBC/Objective) cards were being scraped with their SBC cost
-- estimate shown on list pages, polluting price_snapshots with non-BIN values.

-- 1. Mark untradeable by version name (SBC / Objective rewards).
--    '%Objective%' also matches 'Objectives'.
UPDATE cards SET tradeable = 0
WHERE tradeable = 1
AND (
  version_name LIKE '%SBC%'
  OR version_name LIKE '%Objective%'
);

-- 2. Delete price snapshots violating FUT increment rules (SBC cost estimates,
--    never real BIN prices). Runs BEFORE the 30-day rule below so cards whose
--    only recent data is SBC estimates are correctly caught as untradeable.
--    Ladder: 1k-10k multiples of 100; 10k-50k of 250; 50k-100k of 500; 100k+ of 1000.
DELETE FROM price_snapshots
WHERE (bin_price BETWEEN 1000 AND 9999 AND bin_price % 100 != 0)
   OR (bin_price BETWEEN 10000 AND 49999 AND bin_price % 250 != 0)
   OR (bin_price BETWEEN 50000 AND 99999 AND bin_price % 500 != 0)
   OR (bin_price >= 100000 AND bin_price % 1000 != 0);

-- 3. Mark untradeable: no valid BIN price recorded in the last 30 days.
--    Scrapers restore tradeable=1 when they next see the card with a valid BIN.
UPDATE cards SET tradeable = 0
WHERE tradeable = 1
AND NOT EXISTS (
  SELECT 1 FROM price_snapshots ps
  WHERE ps.card_id = cards.id
  AND ps.bin_price > 0
  AND ps.ts_utc >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days')
);
