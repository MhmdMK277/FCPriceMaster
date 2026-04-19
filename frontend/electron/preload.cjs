const { contextBridge } = require('electron');
const path = require('path');

// Resolve the DB path relative to the project root (two levels up from electron/)
const DB_PATH = path.join(__dirname, '..', '..', '..', 'data', 'fcpricemaster.db');

let db = null;

function getDb() {
  if (!db) {
    try {
      const Database = require('better-sqlite3');
      db = new Database(DB_PATH, { readonly: true });
      db.pragma('journal_mode = WAL');
    } catch (err) {
      console.error('Failed to open DB:', err.message);
      return null;
    }
  }
  return db;
}

contextBridge.exposeInMainWorld('fcdb', {
  getCards: () => {
    const conn = getDb();
    if (!conn) return [];
    return conn.prepare('SELECT * FROM cards ORDER BY id').all();
  },

  getPriceSnapshots: (cardId, platform) => {
    const conn = getDb();
    if (!conn) return [];
    return conn
      .prepare(
        'SELECT * FROM price_snapshots WHERE card_id = ? AND platform = ? ORDER BY ts_utc DESC LIMIT 200'
      )
      .all(cardId, platform);
  },

  getScraperHealth: () => {
    const conn = getDb();
    if (!conn) return [];
    return conn
      .prepare(
        `SELECT source, MAX(run_at_utc) as last_run, success, consecutive_failures, last_error
         FROM scraper_health GROUP BY source`
      )
      .all();
  },
});
