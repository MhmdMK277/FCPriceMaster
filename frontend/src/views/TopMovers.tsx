import { useState, useEffect, useCallback } from 'react';
import type { Platform, TopMoverRow } from '../lib/types';
import { formatPrice, formatChange } from '../lib/formatPrice';

interface Props {
  platform: Platform;
}

export function TopMovers({ platform }: Props) {
  const [rows, setRows] = useState<TopMoverRow[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(async () => {
    const data = await window.fcdb.getTopMovers({ platform, limit: 30 });
    setRows(data);
    setLastRefresh(new Date());
  }, [platform]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <div className="view">
      <div className="view-header">
        <h2>Top Movers <span className="badge">{platform.toUpperCase()}</span></h2>
        <span className="refresh-time">
          {lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : 'Loading…'}
        </span>
      </div>

      {rows.length === 0 ? (
        <div className="empty">No price data in the last 24h. Run the scraper to populate data.</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Player</th>
              <th>Version</th>
              <th className="num">OVR</th>
              <th className="num">POS</th>
              <th className="num">Price</th>
              <th className="num">24h Change</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const up = r.price_change > 0;
              const down = r.price_change < 0;
              return (
                <tr key={r.card_key}>
                  <td className="player-name">{r.player_name}</td>
                  <td><span className="version-badge">{r.version_name}</span></td>
                  <td className="num">{r.rating ?? '—'}</td>
                  <td className="num pos">{r.position ?? '—'}</td>
                  <td className="num price">{formatPrice(r.current_price)}</td>
                  <td className={`num change ${up ? 'up' : down ? 'down' : 'flat'}`}>
                    {r.price_change === 0 ? '—' : formatChange(r.price_change, r.pct_change)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
