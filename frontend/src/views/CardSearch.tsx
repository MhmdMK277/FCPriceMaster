import { useState, useEffect, useCallback } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import type { Platform, CardSearchRow, CardDetailResult } from '../lib/types';
import { formatPrice } from '../lib/formatPrice';

interface Props {
  platform: Platform;
}

function PriceChart({ snapshots }: { snapshots: CardDetailResult['snapshots'] }) {
  const data = snapshots
    .filter(s => s.bin_price !== null)
    .map(s => ({
      ts: new Date(s.ts_utc).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
      price: s.bin_price,
    }));

  if (data.length < 2) {
    return <div className="chart-empty">Not enough data for a chart ({data.length} snapshot{data.length === 1 ? '' : 's'})</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
        <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#64748b' }} />
        <YAxis
          tickFormatter={v => formatPrice(v)}
          tick={{ fontSize: 11, fill: '#64748b' }}
          width={60}
        />
        <Tooltip
          formatter={(v: number) => [formatPrice(v), 'Price']}
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 6 }}
          labelStyle={{ color: '#94a3b8' }}
        />
        <Line type="monotone" dataKey="price" stroke="#38bdf8" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function AttributeGrid({ attrs }: { attrs: CardDetailResult['attrs'] }) {
  const order = ['rating', 'position', 'league', 'nation', 'club'];
  const sorted = [...attrs].sort((a, b) => {
    const ai = order.indexOf(a.key);
    const bi = order.indexOf(b.key);
    if (ai === -1 && bi === -1) return a.key.localeCompare(b.key);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
  return (
    <div className="attr-grid">
      {sorted.map(a => (
        <div key={a.key} className="attr-item">
          <span className="attr-key">{a.key}</span>
          <span className="attr-val">{a.value}</span>
        </div>
      ))}
    </div>
  );
}

export function CardSearch({ platform }: Props) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<CardSearchRow[]>([]);
  const [selected, setSelected] = useState<CardSearchRow | null>(null);
  const [detail, setDetail] = useState<CardDetailResult | null>(null);

  async function search() {
    if (!query.trim()) return;
    const data = await window.fcdb.searchCards({ query: query.trim() });
    setResults(data);
    setSelected(null);
    setDetail(null);
  }

  const loadDetail = useCallback(async () => {
    if (!selected) return;
    const data = await window.fcdb.getCardDetail({ cardKey: selected.card_key, platform });
    setDetail(data);
  }, [selected, platform]);

  useEffect(() => {
    loadDetail();
    const timer = setInterval(loadDetail, 60_000);
    return () => clearInterval(timer);
  }, [loadDetail]);

  return (
    <div className="view card-search">
      <div className="view-header">
        <h2>Card Search <span className="badge">{platform.toUpperCase()}</span></h2>
      </div>

      <div className="search-row">
        <input
          className="search-input"
          placeholder="Search by player name…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && search()}
        />
        <button className="btn" onClick={search}>Search</button>
      </div>

      <div className="search-layout">
        {results.length > 0 && (
          <div className="results-list">
            {results.map(r => (
              <div
                key={r.card_key}
                className={`result-row ${selected?.card_key === r.card_key ? 'selected' : ''}`}
                onClick={() => setSelected(r)}
              >
                <div className="result-name">{r.player_name}</div>
                <div className="result-meta">
                  <span className="version-badge">{r.version_name}</span>
                  {r.rating && <span className="ovr">{r.rating}</span>}
                  {r.position && <span className="pos">{r.position}</span>}
                </div>
              </div>
            ))}
          </div>
        )}

        {detail && (
          <div className="card-detail">
            <div className="detail-header">
              <h3>{detail.card.player_name}</h3>
              <span className="version-badge large">{detail.card.version_name}</span>
            </div>

            <div className="detail-price">
              {detail.snapshots.length > 0
                ? formatPrice(detail.snapshots[detail.snapshots.length - 1].bin_price)
                : '—'
              }
              <span className="price-label">current BIN</span>
            </div>

            <AttributeGrid attrs={detail.attrs} />

            <div className="chart-section">
              <div className="section-title">Price history ({platform.toUpperCase()})</div>
              <PriceChart snapshots={detail.snapshots} />
            </div>

            <div className="snapshot-table-wrap">
              <div className="section-title">Recent snapshots</div>
              <table className="data-table">
                <thead>
                  <tr><th>Time</th><th className="num">Price</th></tr>
                </thead>
                <tbody>
                  {[...detail.snapshots].reverse().slice(0, 10).map((s, i) => (
                    <tr key={i}>
                      <td>{new Date(s.ts_utc).toLocaleString()}</td>
                      <td className="num">{formatPrice(s.bin_price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
