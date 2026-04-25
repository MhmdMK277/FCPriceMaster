import { useState, useEffect, useCallback } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import type { Platform, FodderSummaryRow, FodderSnapshotRow } from '../lib/types';

const RATINGS = [82, 83, 84, 85, 86, 87, 88, 89, 90, 91];

function fmt(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
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

export function Fodder({ platform }: { platform: Platform }) {
  const [summary, setSummary] = useState<FodderSummaryRow[]>([]);
  const [selectedRating, setSelectedRating] = useState<number | null>(null);
  const [chartData, setChartData] = useState<FodderSnapshotRow[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const loadSummary = useCallback(async () => {
    const data = await window.fcdb.getFodderSummary({ platform });
    setSummary(data);
    setLastRefresh(new Date());
  }, [platform]);

  const loadChart = useCallback(async (rating: number) => {
    const data = await window.fcdb.getFodderSnapshot({ rating, platform, hoursBack: 168 });
    setChartData(data);
  }, [platform]);

  useEffect(() => {
    loadSummary();
    const timer = setInterval(loadSummary, 60_000);
    return () => clearInterval(timer);
  }, [loadSummary]);

  useEffect(() => {
    if (selectedRating != null) {
      loadChart(selectedRating);
    }
  }, [selectedRating, platform, loadChart]);

  return (
    <div className="view">
      <div className="view-header">
        <h2>Fodder Prices</h2>
        <span className="fodder-subtitle">Cheapest cards by rating — updated every 30 min</span>
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

            return (
              <tr
                key={rating}
                className={`fodder-row ${isSelected ? 'fodder-row-selected' : ''}`}
                onClick={() => setSelectedRating(isSelected ? null : rating)}
                title="Click to show 7-day chart"
              >
                <td>
                  <span className="fodder-rating">{rating}</span>
                </td>
                <td className="num price">{fmt(row?.cheapest_bin)}</td>
                <td className="num">{fmt(row?.median_bin)}</td>
                <td className={`num ${changeClass(change24h)}`}>{changeStr(change24h)}</td>
                <td className="age">{row?.last_updated ? relTime(row.last_updated) : '—'}</td>
              </tr>
            );
          })}
          {summary.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">No fodder data yet — waiting for first sweep.</td>
            </tr>
          )}
        </tbody>
      </table>

      {selectedRating != null && (
        <div className="fodder-chart-panel">
          <div className="section-title">Rating {selectedRating} — Cheapest BIN (7 days, {platform})</div>
          {chartData.length < 2 ? (
            <div className="chart-empty">Not enough data points yet.</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
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
      )}
    </div>
  );
}
