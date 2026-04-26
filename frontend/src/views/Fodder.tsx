import { useState, useEffect, useCallback } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import type { Platform, FodderSummaryRow, FodderSnapshotRow, FodderCard } from '../lib/types';

const RATINGS = [81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93];

function fmt(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtCoins(n: number): string {
  return n.toLocaleString();
}

function changeClass(change: number | null | undefined): string {
  if (change == null) return 'change flat';
  if (change > 3) return 'change up';
  if (change < -3) return 'change down';
  return 'change flat';
}

function changeStr(change: number | null | undefined): string {
  if (change == null) return '—';
  const sign = change > 0 ? '+' : '';
  return `${sign}${change.toFixed(1)}%`;
}

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function positionColor(pos: string): string {
  const p = pos.toUpperCase();
  if (p === 'GK') return '#f59e0b';
  if (['CB', 'LB', 'RB', 'LWB', 'RWB'].includes(p)) return '#3b82f6';
  if (['CDM', 'CM', 'CAM', 'LM', 'RM'].includes(p)) return '#10b981';
  return '#ef4444';
}


function CardItem({ card }: { card: FodderCard }) {
  const posColor = positionColor(card.position);
  const name = (card.player_name || '—').slice(0, 12);
  const version = (card.card_version || 'Normal').slice(0, 10);
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 4, padding: '6px 8px',
      background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b',
      width: 140, maxWidth: 140, flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        {card.position && (
          <span style={{
            fontSize: 9, fontWeight: 700, color: '#fff', background: posColor,
            borderRadius: 3, padding: '1px 4px', width: 28, textAlign: 'center', flexShrink: 0,
          }}>
            {card.position}
          </span>
        )}
        <span style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1 }}>
          {name}
        </span>
      </div>
      <div style={{ fontSize: 10, color: '#64748b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {version}
      </div>
      <div style={{ fontSize: 13, fontWeight: 700, color: '#fbbf24' }}>
        {fmtCoins(card.bin_price)}
      </div>
    </div>
  );
}

export function Fodder({ platform }: { platform: Platform }) {
  const [summary, setSummary] = useState<FodderSummaryRow[]>([]);
  const [selectedRating, setSelectedRating] = useState<number | null>(null);
  const [cardsByRating, setCardsByRating] = useState<Map<number, FodderCard[]>>(new Map());
  const [chartData, setChartData] = useState<FodderSnapshotRow[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const loadSummary = useCallback(async () => {
    const data = await window.fcdb.getFodderSummary({ platform });
    setSummary(data);
    setLastRefresh(new Date());
  }, [platform]);

  const loadExpanded = useCallback(async (rating: number) => {
    const [cards, history] = await Promise.all([
      window.fcdb.getFodderByRating({ rating, platform, limit: 10 }),
      window.fcdb.getFodderHistory({ rating, platform, hoursBack: 168 }),
    ]);
    setCardsByRating(prev => new Map(prev).set(rating, cards));
    setChartData(history);
  }, [platform]);

  useEffect(() => {
    loadSummary();
    const timer = setInterval(loadSummary, 60_000);
    return () => clearInterval(timer);
  }, [loadSummary]);

  useEffect(() => {
    // Reset expanded state when platform changes
    setSelectedRating(null);
    setCardsByRating(new Map());
    setChartData([]);
  }, [platform]);

  const handleRowClick = async (rating: number) => {
    if (selectedRating === rating) {
      setSelectedRating(null);
      return;
    }
    setSelectedRating(rating);
    await loadExpanded(rating);
  };

  return (
    <div className="view">
      <div className="view-header">
        <h2>Fodder Prices</h2>
        <span className="fodder-subtitle">Cheapest cards by rating — all versions — updated every 30 min</span>
        <span className="refresh-time">
          {lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : 'Loading…'}
        </span>
      </div>

      <table className="data-table fodder-table">
        <thead>
          <tr>
            <th>Rating</th>
            <th className="num">Cheapest BIN</th>
            <th className="num">Median BIN</th>
            <th className="num">24h Change</th>
            <th>Last Updated</th>
          </tr>
        </thead>
        <tbody>
          {RATINGS.map(rating => {
            const row = summary.find(r => r.rating === rating);
            const isSelected = selectedRating === rating;
            const change24h = row?.cheapest_bin && row?.cheapest_bin_24h_ago
              ? ((row.cheapest_bin - row.cheapest_bin_24h_ago) / row.cheapest_bin_24h_ago) * 100
              : null;
            const expandedCards = cardsByRating.get(rating);

            return [
              <tr
                key={`row-${rating}`}
                className={`fodder-row ${isSelected ? 'fodder-row-selected' : ''}`}
                onClick={() => handleRowClick(rating)}
                title="Click to show cards and 7-day chart"
                style={{ cursor: 'pointer' }}
              >
                <td>
                  <span className="fodder-rating">{rating}</span>
                  <span style={{ marginLeft: 6, fontSize: 11, color: '#64748b' }}>
                    {isSelected ? '▲' : '▼'}
                  </span>
                </td>
                <td className="num price">{fmt(row?.cheapest_bin)}</td>
                <td className="num">{fmt(row?.median_bin)}</td>
                <td className={`num ${changeClass(change24h)}`}>{changeStr(change24h)}</td>
                <td className="age">{row?.last_updated ? relTime(row.last_updated) : '—'}</td>
              </tr>,

              isSelected && (
                <tr key={`expand-${rating}`}>
                  <td colSpan={5} style={{ padding: 0, background: '#020617' }}>
                    <div style={{ padding: '12px 16px' }}>
                      {/* Card list */}
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          Top {expandedCards?.length ?? '…'} cheapest {rating}-rated cards
                        </div>
                        {!expandedCards ? (
                          <div style={{ color: '#64748b', fontSize: 13 }}>Loading…</div>
                        ) : expandedCards.length === 0 ? (
                          <div style={{ color: '#64748b', fontSize: 13 }}>No card data yet — waiting for next sweep.</div>
                        ) : (
                          <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 6, maxWidth: '100%' }}>
                            {expandedCards.map(card => (
                              <CardItem key={card.id} card={card} />
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Price chart */}
                      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        7-day cheapest BIN — rating {rating} ({platform})
                      </div>
                      {chartData.length < 2 ? (
                        <div style={{ color: '#64748b', fontSize: 13 }}>Not enough data points yet.</div>
                      ) : (
                        <ResponsiveContainer width="100%" height={160}>
                          <LineChart data={chartData.map(d => ({
                            ts: new Date(d.ts_utc).toLocaleDateString(),
                            price: d.cheapest_bin,
                          }))}>
                            <XAxis dataKey="ts" tick={{ fill: '#64748b', fontSize: 11 }} />
                            <YAxis
                              tick={{ fill: '#64748b', fontSize: 11 }}
                              tickFormatter={v => fmt(v)}
                              width={60}
                            />
                            <Tooltip
                              contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6 }}
                              labelStyle={{ color: '#94a3b8' }}
                              formatter={(v: number) => [fmt(v), 'Cheapest BIN']}
                            />
                            <Line
                              type="monotone"
                              dataKey="price"
                              stroke="#38bdf8"
                              dot={false}
                              strokeWidth={2}
                            />
                          </LineChart>
                        </ResponsiveContainer>
                      )}
                    </div>
                  </td>
                </tr>
              ),
            ];
          })}
          {summary.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">No fodder data yet — waiting for first sweep.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
