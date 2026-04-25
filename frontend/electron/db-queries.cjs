/**
 * Shared DB query functions used by both ipcMain handlers and --selftest mode.
 * All functions receive an open better-sqlite3 Database instance and return
 * plain JSON-serializable objects.
 */

const TOP_MOVERS_SQL = `
  WITH snaps AS (
    SELECT card_id, bin_price, ts_utc,
      ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY ts_utc DESC) AS rn_latest,
      ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY ts_utc ASC)  AS rn_oldest
    FROM price_snapshots
    WHERE platform = ? AND ts_utc >= datetime('now', ? || ' hours')
  ),
  latest AS (SELECT card_id, bin_price, ts_utc FROM snaps WHERE rn_latest = 1),
  oldest AS (SELECT card_id, bin_price, ts_utc FROM snaps WHERE rn_oldest = 1)
  SELECT
    c.id, c.card_key, c.player_name, c.version_name,
    l.bin_price  AS current_price,
    o.bin_price  AS price_24h_ago,
    (l.bin_price - o.bin_price) AS price_change,
    CASE WHEN o.bin_price > 0 THEN ROUND(100.0*(l.bin_price-o.bin_price)/o.bin_price,1) ELSE 0.0 END AS pct_change,
    (SELECT value FROM card_attributes ca WHERE ca.card_id=c.id AND ca.key=? LIMIT 1) AS rating,
    (SELECT value FROM card_attributes ca WHERE ca.card_id=c.id AND ca.key=? LIMIT 1) AS position
  FROM cards c
  JOIN latest l ON c.id = l.card_id
  JOIN oldest o ON c.id = o.card_id
  ORDER BY ABS(l.bin_price - o.bin_price) DESC
  LIMIT ?`;

const SEARCH_CARDS_SQL = `
  SELECT c.id, c.card_key, c.player_name, c.version_name, c.game_edition,
    (SELECT value FROM card_attributes ca WHERE ca.card_id=c.id AND ca.key=? LIMIT 1) AS rating,
    (SELECT value FROM card_attributes ca WHERE ca.card_id=c.id AND ca.key=? LIMIT 1) AS position,
    (SELECT value FROM card_attributes ca WHERE ca.card_id=c.id AND ca.key=? LIMIT 1) AS league
  FROM cards c
  WHERE c.player_name LIKE ? OR c.version_name LIKE ? OR c.card_key LIKE ?
  ORDER BY c.player_name
  LIMIT 20`;

const SCRAPER_HEALTH_SQL = `
  SELECT sh.*
  FROM scraper_health sh
  WHERE sh.run_at_utc = (
    SELECT MAX(sh2.run_at_utc) FROM scraper_health sh2 WHERE sh2.source = sh.source
  )
  ORDER BY sh.source`;

function getTopMovers(db, { platform, hoursBack = 24, limit = 30 } = {}) {
  const hoursStr = `-${Math.abs(parseInt(hoursBack, 10))}`;
  return db.prepare(TOP_MOVERS_SQL).all(platform, hoursStr, 'rating', 'position', limit);
}

function searchCards(db, { query, limit = 20 } = {}) {
  const like = `%${query}%`;
  return db.prepare(SEARCH_CARDS_SQL).all('rating', 'position', 'league', like, like, like);
}

function getCardDetail(db, { cardKey, platform } = {}) {
  const card = db.prepare(`SELECT * FROM cards WHERE card_key = ?`).get(cardKey);
  if (!card) return null;
  const attrs = db.prepare(
    `SELECT key, value FROM card_attributes WHERE card_id = ? ORDER BY key`
  ).all(card.id);
  const snapshots = db.prepare(`
    SELECT ts_utc, bin_price, volume_proxy
    FROM price_snapshots
    WHERE card_id = ? AND platform = ?
    ORDER BY ts_utc ASC
    LIMIT 200
  `).all(card.id, platform);
  return { card, attrs, snapshots };
}

function getScraperHealth(db, { limit = 50 } = {}) {
  return db.prepare(SCRAPER_HEALTH_SQL).all();
}

const RECENT_SIGNALS_SQL = `
  SELECT
    s.id, s.source, s.source_server, s.signal_type,
    s.ts_utc, s.original_author, s.original_ts_utc,
    s.raw_text, s.has_attachments,
    COALESCE(s.signal_category, '') AS signal_category,
    COALESCE(s.priority, 'medium')  AS priority
  FROM signals s
  WHERE s.ts_utc >= datetime('now', ? || ' hours')
    AND (? IS NULL OR s.source = ? OR s.source_server = ?)
  ORDER BY s.ts_utc DESC
  LIMIT ?`;

const SIGNAL_ATTACHMENTS_SQL = `
  SELECT url FROM signal_attachments WHERE signal_id = ?`;

function getRecentSignals(db, { limit = 100, hoursBack = 24, sourceFilter = null } = {}) {
  const hoursStr = `-${Math.abs(parseInt(hoursBack, 10))}`;
  // sourceFilter matches against both source column (e.g. 'twitter') and source_server
  const rows = db.prepare(RECENT_SIGNALS_SQL).all(hoursStr, sourceFilter, sourceFilter, sourceFilter, limit);
  return rows.map(row => ({
    ...row,
    attachment_urls: row.has_attachments
      ? db.prepare(SIGNAL_ATTACHMENTS_SQL).all(row.id).map(r => r.url)
      : [],
  }));
}

// ---------------------------------------------------------------------------
// Fodder queries
// ---------------------------------------------------------------------------

const FODDER_SUMMARY_SQL = `
  WITH latest AS (
    SELECT rating, cheapest_bin, median_bin, ts_utc,
      ROW_NUMBER() OVER (PARTITION BY rating ORDER BY ts_utc DESC) AS rn
    FROM fodder_snapshots
    WHERE platform = ?
  ),
  prev_24h AS (
    SELECT rating, cheapest_bin,
      ROW_NUMBER() OVER (PARTITION BY rating ORDER BY ts_utc DESC) AS rn
    FROM fodder_snapshots
    WHERE platform = ? AND ts_utc <= datetime('now', '-24 hours')
  )
  SELECT l.rating, l.cheapest_bin, l.median_bin, l.ts_utc AS last_updated,
    p.cheapest_bin AS cheapest_bin_24h_ago
  FROM latest l
  LEFT JOIN prev_24h p ON l.rating = p.rating AND p.rn = 1
  WHERE l.rn = 1
  ORDER BY l.rating`;

const FODDER_SNAPSHOT_SQL = `
  SELECT rating, platform, ts_utc, cheapest_bin, second_cheapest_bin, median_bin
  FROM fodder_snapshots
  WHERE rating = ? AND platform = ? AND ts_utc >= datetime('now', ? || ' hours')
  ORDER BY ts_utc ASC`;

const FODDER_BY_RATING_SQL = `
  SELECT fc.id, fc.card_key, fc.player_name, fc.rating, fc.position,
         fc.club_name, fc.nation_name, fc.club_badge_url, fc.nation_flag_url,
         fc.card_version, fc.bin_price, fc.rank_in_rating, fc.ts_utc, fc.platform
  FROM fodder_cards fc
  WHERE fc.snapshot_id = (
    SELECT id FROM fodder_snapshots
    WHERE rating = ? AND platform = ?
    ORDER BY ts_utc DESC LIMIT 1
  )
  ORDER BY fc.rank_in_rating ASC
  LIMIT ?`;

const FODDER_HISTORY_SQL = `
  SELECT rating, platform, ts_utc, cheapest_bin, second_cheapest_bin, median_bin
  FROM fodder_snapshots
  WHERE rating = ? AND platform = ? AND ts_utc >= datetime('now', ? || ' hours')
  ORDER BY ts_utc ASC`;

function getFodderSummary(db, { platform } = {}) {
  try {
    return db.prepare(FODDER_SUMMARY_SQL).all(platform, platform);
  } catch { return []; }
}

function getFodderSnapshot(db, { rating, platform, hoursBack = 168 } = {}) {
  const hoursStr = `-${Math.abs(parseInt(hoursBack, 10))}`;
  try {
    return db.prepare(FODDER_SNAPSHOT_SQL).all(rating, platform, hoursStr);
  } catch { return []; }
}

function getFodderByRating(db, { rating, platform, limit = 10 } = {}) {
  try {
    return db.prepare(FODDER_BY_RATING_SQL).all(rating, platform, limit);
  } catch { return []; }
}

function getFodderHistory(db, { rating, platform, hoursBack = 168 } = {}) {
  const hoursStr = `-${Math.abs(parseInt(hoursBack, 10))}`;
  try {
    return db.prepare(FODDER_HISTORY_SQL).all(rating, platform, hoursStr);
  } catch { return []; }
}

// ---------------------------------------------------------------------------
// LLM history query
// ---------------------------------------------------------------------------

const LLM_HISTORY_SQL = `
  SELECT id, ts_utc, model, input_tokens, output_tokens, cost_usd, feature, input_text, output_json
  FROM llm_calls
  WHERE ts_utc >= datetime('now', '-1 day')
  ORDER BY ts_utc DESC
  LIMIT ?`;

function getLLMHistory(db, { limit = 10 } = {}) {
  try {
    return db.prepare(LLM_HISTORY_SQL).all(limit);
  } catch { return []; }
}

module.exports = {
  getTopMovers, searchCards, getCardDetail, getScraperHealth, getRecentSignals,
  getFodderSummary, getFodderSnapshot, getFodderByRating, getFodderHistory, getLLMHistory,
};
