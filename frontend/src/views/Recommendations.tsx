import { useState, useEffect, useCallback, useRef } from 'react';

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
  prior_count: number;
  model_id?: string;
}

const MODEL_DISPLAY: Record<string, { label: string; isNvidia: boolean }> = {
  'claude-haiku-4-5-20251001':              { label: 'Claude Haiku',    isNvidia: false },
  'deepseek-ai/deepseek-v4-pro':            { label: 'DeepSeek V4 Pro', isNvidia: true },
  'moonshotai/kimi-k2.6':                   { label: 'Kimi K2.6',       isNvidia: true },
  'qwen/qwen3-next-80b-a3b-instruct':       { label: 'Qwen3 80B',        isNvidia: true },
  'mistralai/mistral-small-4-119b-2603':    { label: 'Mistral Small',    isNvidia: true },
  'openai/gpt-oss-120b':                    { label: 'GPT OSS 120B',    isNvidia: true },
  'structural':                             { label: 'Structural',       isNvidia: false },
};

function getModelDisplay(modelId: string): { label: string; isNvidia: boolean } {
  if (MODEL_DISPLAY[modelId]) return MODEL_DISPLAY[modelId];
  const lower = modelId.toLowerCase();
  const isNvidia = lower.includes('deepseek') || lower.includes('qwen') || lower.includes('mistral') ||
    lower.includes('kimi') || lower.includes('gpt-oss') || lower.includes('moonshotai') || lower.includes('nvapi');
  return { label: modelId.split('/').pop() || modelId, isNvidia };
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

interface BudgetStatus {
  spent_today_usd: number;
  cap_usd: number;
  remaining_usd: number;
  can_generate: boolean;
}

interface Toast {
  message: string;
  type: 'info' | 'success' | 'error';
}

const CALL_COLORS: Record<string, string> = {
  buy: '#22c55e',
  avoid: '#ef4444',
  hold: '#6b7280',
};

const MODEL_OPTIONS = [
  { id: 'haiku', label: 'Claude Haiku' },
  { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { id: 'kimi-k2-6', label: 'Kimi K2.6' },
  { id: 'qwen3-80b', label: 'Qwen3 80B' },
  { id: 'mistral-small', label: 'Mistral Small' },
  { id: 'gpt-oss-120b', label: 'GPT OSS 120B' },
];

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
  const [budget, setBudget] = useState<BudgetStatus | null>(null);
  const [showDismissed, setShowDismissed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [providerId, setProviderId] = useState(() => localStorage.getItem('rec_provider_id') || 'haiku');
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(t: Toast) {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(t);
    toastTimer.current = setTimeout(() => setToast(null), 5000);
  }

  const load = useCallback(async () => {
    try {
      const [recsData, statsData, budgetData] = await Promise.all([
        (window as any).fcdb.getRecommendations({
          platform, limit: 50, activeOnly: !showDismissed, showAll,
        }),
        (window as any).fcdb.getRecommendationStats({ days: 7 }),
        (window as any).fcdb.getRecommendationBudgetStatus(),
      ]);
      setRecs(recsData || []);
      setStats(statsData || null);
      setBudget(budgetData || null);
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'Failed to load');
    }
  }, [platform, showDismissed, showAll]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    localStorage.setItem('rec_provider_id', providerId);
  }, [providerId]);

  async function handleRefresh() {
    if (providerId === 'haiku' && budget && !budget.can_generate) return;
    setGenerating(true);
    setError(null);
    try {
      const res = await (window as any).fcdb.triggerRecommendations({ platform, provider_id: providerId });
      if (res?.status === 'error') {
        setError(`Generation failed: ${res.error}`);
      } else if (res?.skipped) {
        showToast({ message: `Nothing to generate right now: ${res.reason}`, type: 'info' });
      } else if (res?.recs_added > 0) {
        showToast({ message: `${res.recs_added} new recommendation${res.recs_added !== 1 ? 's' : ''} added`, type: 'success' });
        await load();
      } else {
        await load();
      }
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

  const isFreeProvider = providerId !== 'haiku';
  const budgetExhausted = !isFreeProvider && budget !== null && !budget.can_generate;
  const visible = showDismissed ? recs : recs.filter(r => !r.dismissed_at);

  const toastBg: Record<string, string> = {
    info: '#1e3a5f',
    success: '#14532d',
    error: '#450a0a',
  };
  const toastColor: Record<string, string> = {
    info: '#93c5fd',
    success: '#86efac',
    error: '#fca5a5',
  };

  return (
    <div style={{ padding: '24px', maxWidth: 860, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h2 style={{ margin: 0, color: '#f1f5f9', fontSize: 22 }}>Recommendations</h2>
        <div style={{ flex: 1 }} />
        <label style={{ color: '#94a3b8', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={showDismissed} onChange={e => setShowDismissed(e.target.checked)} />
          Show dismissed
        </label>
        <label style={{ color: '#94a3b8', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
          Show all history
        </label>
        <label style={{ color: '#94a3b8', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          Model:
          <select
            value={providerId}
            onChange={e => setProviderId(e.target.value)}
            style={{
              background: '#0f172a',
              color: '#e2e8f0',
              border: '1px solid #334155',
              borderRadius: 5,
              padding: '5px 8px',
              fontSize: 12,
            }}
          >
            {MODEL_OPTIONS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        </label>
        <div title={budgetExhausted ? `Daily AI budget used ($${budget?.cap_usd.toFixed(2)}). Resets at midnight UTC.` : undefined}>
          <button
            onClick={handleRefresh}
            disabled={generating || budgetExhausted}
            style={{
              background: budgetExhausted ? '#334155' : '#3b82f6',
              color: budgetExhausted ? '#64748b' : '#fff',
              border: 'none', borderRadius: 6,
              padding: '7px 16px',
              cursor: generating || budgetExhausted ? 'not-allowed' : 'pointer',
              opacity: generating ? 0.6 : 1,
              fontSize: 13,
            }}
          >
            {generating ? 'Generating…' : budgetExhausted ? 'Budget used' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div style={{
          background: toastBg[toast.type], color: toastColor[toast.type],
          borderRadius: 8, padding: '10px 16px', marginBottom: 16, fontSize: 13,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span>{toast.message}</span>
          <span
            style={{ cursor: 'pointer', marginLeft: 16, opacity: 0.7 }}
            onClick={() => setToast(null)}
          >✕</span>
        </div>
      )}

      {/* Stats bar */}
      {stats && (
        <div style={{
          background: '#1e293b', borderRadius: 8, padding: '10px 16px',
          marginBottom: 16, display: 'flex', gap: 24, fontSize: 13, color: '#94a3b8',
          flexWrap: 'wrap',
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

      {/* Budget bar */}
      {budget && (
        <div style={{
          background: '#1e293b', borderRadius: 8, padding: '8px 16px',
          marginBottom: 20, display: 'flex', gap: 20, fontSize: 12, color: '#64748b',
        }}>
          {isFreeProvider ? (
            <span>
              Model: <strong style={{ color: '#22c55e' }}>
                {MODEL_OPTIONS.find(m => m.id === providerId)?.label || providerId}
              </strong>{' · '}
              <strong style={{ color: '#22c55e' }}>Free (NVIDIA · 40 RPM)</strong>
            </span>
          ) : (
            <>
              <span>Daily AI budget: <strong style={{ color: budgetExhausted ? '#ef4444' : '#94a3b8' }}>
                ${budget.spent_today_usd.toFixed(4)} / ${budget.cap_usd.toFixed(2)}
              </strong></span>
              <span>Remaining: <strong style={{ color: budgetExhausted ? '#ef4444' : '#22c55e' }}>
                ${budget.remaining_usd.toFixed(4)}
              </strong></span>
            </>
          )}
          {!isFreeProvider && budgetExhausted && (
            <span style={{ color: '#94a3b8' }}>Resets at midnight UTC</span>
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
          {!budgetExhausted && (
            <span style={{ color: '#3b82f6', cursor: 'pointer' }} onClick={handleRefresh}>
              Generate now
            </span>
          )}
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

              {/* Card name + prior count */}
              <div style={{ color: '#f1f5f9', fontSize: 16, fontWeight: 600, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>
                  {dismissed
                    ? <s>{rec.card_name} — {rec.version_name}</s>
                    : `${rec.card_name} — ${rec.version_name}`}
                </span>
                <span style={{ color: '#475569', fontSize: 12 }}>{rec.platform.toUpperCase()}</span>
                {rec.prior_count > 1 && (
                  <span style={{
                    color: '#64748b', fontSize: 11, background: '#0f172a',
                    borderRadius: 4, padding: '1px 6px',
                  }}>
                    {rec.prior_count - 1} previous
                  </span>
                )}
                {rec.model_id && (() => {
                  const mi = getModelDisplay(rec.model_id);
                  return (
                    <span style={{
                      fontSize: 11, padding: '1px 7px', borderRadius: 3,
                      background: mi.isNvidia ? '#122d1b' : '#2a1e3b',
                      color: mi.isNvidia ? '#22c55e' : '#8b5cf6',
                    }}>
                      {mi.label}
                    </span>
                  );
                })()}
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
