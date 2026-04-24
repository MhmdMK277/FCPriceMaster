import { useState, useEffect, useCallback } from 'react';
import type { ScraperHealthRow } from '../lib/types';

export function ScraperHealth() {
  const [rows, setRows] = useState<ScraperHealthRow[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(async () => {
    const data = await window.fcdb.getScraperHealth();
    setRows(data);
    setLastRefresh(new Date());
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <div className="view">
      <div className="view-header">
        <h2>Scraper Health</h2>
        <span className="refresh-time">
          {lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : 'Loading…'}
        </span>
      </div>

      {rows.length === 0 ? (
        <div className="empty">No scraper runs recorded yet.</div>
      ) : (
        <div className="health-cards">
          {rows.map(r => (
            <HealthCard key={r.id} row={r} />
          ))}
        </div>
      )}
    </div>
  );
}

function HealthCard({ row }: { row: ScraperHealthRow }) {
  const ok = row.success === 1;
  const failing = row.consecutive_failures > 0;
  const status = ok ? 'ok' : failing ? 'failing' : 'error';

  const age = row.run_at_utc
    ? Math.round((Date.now() - new Date(row.run_at_utc).getTime()) / 60_000)
    : null;

  return (
    <div className={`health-card ${status}`}>
      <div className="health-header">
        <span className="source-name">{row.source}</span>
        <span className={`status-dot ${status}`} title={status} />
      </div>
      <div className="health-meta">
        <div className="meta-row">
          <span className="meta-label">Last run</span>
          <span>{row.run_at_utc ? new Date(row.run_at_utc).toLocaleString() : '—'}</span>
          {age !== null && <span className="age">({age}m ago)</span>}
        </div>
        <div className="meta-row">
          <span className="meta-label">Records written</span>
          <span>{row.records_written ?? 0}</span>
        </div>
        {row.consecutive_failures > 0 && (
          <div className="meta-row warning">
            <span className="meta-label">Consecutive failures</span>
            <span>{row.consecutive_failures}</span>
          </div>
        )}
        {row.last_error && (
          <div className="error-text">{row.last_error}</div>
        )}
      </div>
    </div>
  );
}
