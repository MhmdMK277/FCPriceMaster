import { useState, useEffect, useCallback } from 'react';

interface Recommendation {
  id: number;
  card_name: string;
  version_name: string;
  platform: string;
  call: string;
  confidence: number;
  horizon_hours: number | null;
  target_price: number | null;
  reasoning: string | null;
  ts_utc: string;
  outcome_verdict: string | null;
  dismissed_at: string | null;
}

interface RecStats {
  total_evaluated: number;
  correct: number;
  incorrect: number;
  neutral: number;
  accuracy_pct: number | null;
  buy_total: number;
  avoid_total: number;
  next_eval_in_hours: number | null;
  oldest_pending_hours: number | null;
}

const CALL_COLORS: Record<string, string> = {
  buy: '#22c55e',
  avoid: '#ef4444',
  hold: '#6b7280',
};

const OUTCOME_LABELS: Record<string, string> = {
  correct: '✅ Correct',
  incorrect: '❌ Incorrect',
  neutral: '⚪ Neutral',
  expired: '💤 Expired',
};

function horizonLabel(hours: number | null): string {
  if (!hours) return '?';
  if (hours <= 24) return 'Short (hours)';
  if (hours <= 96) return 'Medium (days)';
  return 'Long (weeks)';
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const h = Math.floor(diff / 3600000);
  if (h < 1) return 'just now';
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function Recommendations({ platform }: { platform: string }) {
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [stats, setStats] = useState<RecStats | null>(null);
  const [showDismissed, setShowDismissed] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [recsData, statsData] = await Promise.all([
        (window as any).fcdb.getRecommendations({ platform, limit: 50, activeOnly: !showDismissed }),
        (window as any).fcdb.getRecommendationStats({ days: 7 }),
      ]);
      setRecs(recsData || []);
      setStats(statsData || null);
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'Failed to load');
    }
  }, [platform, showDismissed]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  async function handleRefresh() {
    setGenerating(true);
    setError(null);
    try {
      const res = await (window as any).fcdb.triggerRecommendations({ platform });
      if (res?.error) setError(res.error);
      else await load();
    } catch (e: any) {
      setError(e?.message || 'Failed to trigger');
    } finally {
      setGenerating(false);
    }
  }

  async function handleDismiss(id: number) {
    try {
      await (window as any).fcdb.dismissRecommendation({ id });
      setRecs(prev => prev.filter(r => r.id !== id));
    } catch {}
  }

  const visible = showDismissed ? recs : recs.filter(r => !r.dismissed_at);

  return (
    <div style={{ padding: '24px', maxWidth: 860, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h2 style={{ margin: 0, color: '#f1f5f9', fontSize: 22 }}>Recommendations</h2>
        <div style={{ flex: 1 }} />
        <label style={{ color: '#94a3b8', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={showDismissed}
            onChange={e => setShowDismissed(e.target.checked)}
          />
          Show dismissed
        </label>
        <button
          onClick={handleRefresh}
          disabled={generating}
          style={{
            background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6,
            padding: '7px 16px', cursor: generating ? 'not-allowed' : 'pointer',
            opacity: generating ? 0.6 : 1, fontSize: 13,
          }}
        >
          {generating ? 'Generating…' : 'Refresh'}
        </button>
      </div>

      {/* Stats bar */}
      {stats && (
        <div style={{
          background: '#1e293b', borderRadius: 8, padding: '10px 16px',
          marginBottom: 20, display: 'flex', gap: 24, fontSize: 13, color: '#94a3b8',
        }}>
          <span>Last 7 days: <strong style={{ color: '#f1f5f9' }}>{stats.total_evaluated}</strong> evaluated</span>
          {stats.accuracy_pct !== null && (
            <span>Accuracy: <strong style={{ color: '#22c55e' }}>{stats.accuracy_pct.toFixed(0)}%</strong></span>
          )}
          <span>Buys: <strong style={{ color: '#22c55e' }}>{stats.buy_total}</strong></span>
          <span>Avoids: <strong style={{ color: '#ef4444' }}>{stats.avoid_total}</strong></span>
          {stats.next_eval_in_hours !== null && (
            <span style={{ marginLeft: 'auto' }}>
              Next eval: <strong style={{ color: '#f1f5f9' }}>in {stats.next_eval_in_hours}h</strong>
            </span>
          )}
          {stats.oldest_pending_hours !== null && (
            <span>Oldest pending: <strong style={{ color: '#f1f5f9' }}>{stats.oldest_pending_hours}h old</strong></span>
          )}
        </div>
      )}

      {error && (
        <div style={{ background: '#450a0a', color: '#fca5a5', borderRadius: 8, padding: '10px 16px', marginBottom: 16, fontSize: 13 }}>
          {error}
        </div>
      )}

      {visible.length === 0 && !generating && (
        <div style={{ color: '#64748b', textAlign: 'center', marginTop: 60, fontSize: 15 }}>
          No recommendations yet.{' '}
          <span style={{ color: '#3b82f6', cursor: 'pointer' }} onClick={handleRefresh}>
            Generate now
          </span>
        </div>
      )}

      {/* Recommendation cards */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {visible.map(rec => {
          const callColor = CALL_COLORS[rec.call] || '#6b7280';
          const dismissed = !!rec.dismissed_at;
          return (
            <div
              key={rec.id}
              style={{
                background: '#1e293b',
                borderRadius: 10,
                borderLeft: `4px solid ${callColor}`,
                padding: '16px 20px',
                opacity: dismissed ? 0.5 : 1,
                position: 'relative',
              }}
            >
              {/* Top row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                <span style={{
                  background: callColor, color: '#fff', borderRadius: 4,
                  padding: '2px 10px', fontSize: 12, fontWeight: 700, textTransform: 'uppercase',
                }}>
                  {rec.call}
                </span>
                <span style={{ color: '#94a3b8', fontSize: 13 }}>
                  {rec.confidence.toFixed(0)}% confidence
                </span>
                <span style={{ color: '#64748b', fontSize: 12 }}>·</span>
                <span style={{ color: '#94a3b8', fontSize: 13 }}>
                  {horizonLabel(rec.horizon_hours)}
                </span>
                <div style={{ flex: 1 }} />
                <span style={{ color: '#475569', fontSize: 12 }}>
                  {timeAgo(rec.ts_utc)}
                </span>
              </div>

              {/* Card name */}
              <div style={{ color: '#f1f5f9', fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
                {dismissed ? <s>{rec.card_name} — {rec.version_name}</s> : `${rec.card_name} — ${rec.version_name}`}
                <span style={{ color: '#475569', fontSize: 12, marginLeft: 10 }}>{rec.platform.toUpperCase()}</span>
              </div>

              {/* Prices */}
              {rec.target_price && (
                <div style={{ color: '#94a3b8', fontSize: 13, marginBottom: 8 }}>
                  {rec.call === 'buy'
                    ? `Buy at: ${rec.target_price.toLocaleString()} coins`
                    : `Target: ${rec.target_price.toLocaleString()} coins`}
                </div>
              )}

              {/* Reasoning */}
              {rec.reasoning && (
                <div style={{
                  color: '#cbd5e1', fontSize: 13, lineHeight: 1.5,
                  marginBottom: 12, fontStyle: 'italic',
                }}>
                  "{rec.reasoning}"
                </div>
              )}

              {/* Footer */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                {!dismissed && (
                  <button
                    onClick={() => handleDismiss(rec.id)}
                    style={{
                      background: 'transparent', color: '#475569', border: '1px solid #334155',
                      borderRadius: 5, padding: '4px 12px', cursor: 'pointer', fontSize: 12,
                    }}
                  >
                    Dismiss
                  </button>
                )}
                <div style={{ flex: 1 }} />
                {rec.outcome_verdict ? (
                  <span style={{ fontSize: 13, color: '#94a3b8' }}>
                    {OUTCOME_LABELS[rec.outcome_verdict] || rec.outcome_verdict}
                  </span>
                ) : (
                  <span style={{ fontSize: 13, color: '#475569' }}>⏳ Pending</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
