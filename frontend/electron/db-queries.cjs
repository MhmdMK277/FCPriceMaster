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
    COALESCE(s.priority, 'medium')  AS priority,
    COALESCE(s.signal_context, 'fut_market') AS signal_context
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
  WHERE ts_utc >= datetime('now', '-7 days')
    AND feature IN ('ask', 'ask_multi')
  ORDER BY ts_utc DESC
  LIMIT ?`;

function getLLMHistory(db, { limit = 10 } = {}) {
  try {
    return db.prepare(LLM_HISTORY_SQL).all(limit);
  } catch { return []; }
}

// ---------------------------------------------------------------------------
// Recommendations queries
// ---------------------------------------------------------------------------

// Deduped: for non-fodder cards, show only the most recent rec per player+version.
// Includes prior_count so the UI can show "(N previous)".
const GET_RECS_DEDUPED_SQL = `
  WITH card_latest AS (
    SELECT MAX(r.id) AS id, COUNT(*) AS prior_count
    FROM recommendations r
    JOIN cards c ON c.id = r.card_id
    WHERE r.platform = ? AND (? = 0 OR r.dismissed_at IS NULL)
    GROUP BY c.player_name, c.version_name, r.platform
  ),
  fodder_all AS (
    SELECT r.id, 1 AS prior_count
    FROM recommendations r
    WHERE r.card_id IS NULL AND r.platform = ? AND (? = 0 OR r.dismissed_at IS NULL)
  ),
  combined AS (
    SELECT id, prior_count FROM card_latest
    UNION ALL
    SELECT id, prior_count FROM fodder_all
  )
  SELECT r.id, r.card_id,
    COALESCE(c.player_name, r.reasoning) AS card_name,
    COALESCE(c.version_name, 'fodder')   AS version_name,
    r.platform, r.call, r.confidence, r.horizon_hours,
    r.target_price, r.reasoning, r.ts_utc, r.dismissed_at, r.dismissed_reason,
    o.verdict AS outcome_verdict,
    com.prior_count,
    COALESCE(r.model_id, 'claude-haiku-4-5-20251001') AS model_id
  FROM combined com
  JOIN recommendations r ON r.id = com.id
  LEFT JOIN cards c ON c.id = r.card_id
  LEFT JOIN outcomes o ON o.recommendation_id = r.id
  ORDER BY r.ts_utc DESC, r.confidence DESC
  LIMIT ?`;

// showAll=true: every rec row, no grouping
const GET_RECS_ALL_SQL = `
  SELECT r.id, r.card_id,
    COALESCE(c.player_name, r.reasoning) AS card_name,
    COALESCE(c.version_name, 'fodder')   AS version_name,
    r.platform, r.call, r.confidence, r.horizon_hours,
    r.target_price, r.reasoning, r.ts_utc, r.dismissed_at, r.dismissed_reason,
    o.verdict AS outcome_verdict,
    1 AS prior_count,
    COALESCE(r.model_id, 'claude-haiku-4-5-20251001') AS model_id
  FROM recommendations r
  LEFT JOIN cards c ON c.id = r.card_id
  LEFT JOIN outcomes o ON o.recommendation_id = r.id
  WHERE r.platform = ?
    AND (? = 0 OR r.dismissed_at IS NULL)
  ORDER BY r.ts_utc DESC, r.confidence DESC
  LIMIT ?`;

// COALESCE: SUM over zero rows is NULL, which React renders as a blank
// ("Buys: " with no number, session 40). Zero evaluated must read as 0.
const REC_STATS_SQL = `
  SELECT
    COUNT(*) AS total_evaluated,
    COALESCE(SUM(CASE WHEN o.verdict = 'correct'   THEN 1 ELSE 0 END), 0) AS correct,
    COALESCE(SUM(CASE WHEN o.verdict = 'incorrect' THEN 1 ELSE 0 END), 0) AS incorrect,
    COALESCE(SUM(CASE WHEN o.verdict = 'neutral'   THEN 1 ELSE 0 END), 0) AS neutral,
    COALESCE(SUM(CASE WHEN r.call = 'buy'   THEN 1 ELSE 0 END), 0) AS buy_total,
    COALESCE(SUM(CASE WHEN r.call = 'avoid' THEN 1 ELSE 0 END), 0) AS avoid_total
  FROM recommendations r
  JOIN outcomes o ON o.recommendation_id = r.id
  WHERE r.ts_utc >= datetime('now', ? || ' days')`;

function getRecommendations(db, { platform, limit = 50, activeOnly = true, showAll = false } = {}) {
  try {
    const activeFlag = activeOnly ? 1 : 0;
    if (showAll) {
      return db.prepare(GET_RECS_ALL_SQL).all(platform, activeFlag, limit);
    }
    // Deduped: params are (platform, activeFlag, platform, activeFlag, limit)
    return db.prepare(GET_RECS_DEDUPED_SQL).all(platform, activeFlag, platform, activeFlag, limit);
  } catch { return []; }
}

function getRecommendationBudgetStatus(db) {
  const AUTONOMOUS_CAP = 0.02;
  const today = new Date().toISOString().slice(0, 10);
  try {
    const row = db.prepare(
      `SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM llm_calls WHERE ts_utc >= ? AND feature = 'autonomous'`
    ).get(today + 'T00:00:00Z');
    const spent = row ? row.spent : 0;
    return {
      spent_today_usd: Math.round(spent * 1e6) / 1e6,
      cap_usd: AUTONOMOUS_CAP,
      remaining_usd: Math.max(0, Math.round((AUTONOMOUS_CAP - spent) * 1e6) / 1e6),
      can_generate: spent <= AUTONOMOUS_CAP,
    };
  } catch {
    return { spent_today_usd: 0, cap_usd: AUTONOMOUS_CAP, remaining_usd: AUTONOMOUS_CAP, can_generate: true };
  }
}

function dismissRecommendation(db, { id, reason = null } = {}) {
  try {
    db.prepare(
      `UPDATE recommendations
       SET dismissed = 1, dismissed_reason = ?, dismissed_at = datetime('now')
       WHERE id = ?`
    ).run(reason, id);
    return { ok: true };
  } catch (e) { return { ok: false, error: e.message }; }
}

function getRecommendationStats(db, { days = 7 } = {}) {
  try {
    const row = db.prepare(REC_STATS_SQL).get(`-${Math.abs(parseInt(days, 10))}`);
    if (!row) return null;
    const { total_evaluated, correct, incorrect, neutral, buy_total, avoid_total } = row;
    const accuracy_pct = total_evaluated > 0
      ? Math.round(100 * correct / (correct + incorrect || 1))
      : null;

    // Time since last outcome_evaluator run (evaluator fires every 6h)
    const lastEvalRow = db.prepare(
      `SELECT MAX(evaluated_at_utc) as last_eval FROM outcomes`
    ).get();
    let next_eval_in_hours = null;
    if (lastEvalRow && lastEvalRow.last_eval) {
      const hoursSince = (Date.now() - new Date(lastEvalRow.last_eval).getTime()) / 3600000;
      next_eval_in_hours = Math.max(0, Math.round(6 - hoursSince));
    }

    // Oldest active recommendation with no outcome yet
    const oldestRow = db.prepare(
      `SELECT MIN(ts_utc) as oldest_ts FROM recommendations
       WHERE dismissed_at IS NULL
         AND NOT EXISTS (SELECT 1 FROM outcomes WHERE recommendation_id = recommendations.id)`
    ).get();
    let oldest_pending_hours = null;
    if (oldestRow && oldestRow.oldest_ts) {
      const parsed = new Date(oldestRow.oldest_ts).getTime();
      if (!isNaN(parsed)) {
        oldest_pending_hours = Math.round((Date.now() - parsed) / 3600000);
      }
    }

    return { total_evaluated, correct, incorrect, neutral, accuracy_pct, buy_total, avoid_total,
             next_eval_in_hours, oldest_pending_hours };
  } catch { return null; }
}

module.exports = {
  getTopMovers, searchCards, getCardDetail, getScraperHealth, getRecentSignals,
  getFodderSummary, getFodderSnapshot, getFodderByRating, getFodderHistory, getLLMHistory,
  getRecommendations, dismissRecommendation, getRecommendationStats, getRecommendationBudgetStatus,
};
