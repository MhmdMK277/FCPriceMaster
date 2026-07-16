import { useState, useEffect, useCallback, useRef } from 'react';

interface Recommendation {
  id: number;
  card_id: number | null;
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
  dismissed_reason: string | null;
  prior_count: number;
  model_id?: string;
}

const DISMISS_REASONS = [
  'Wrong price data',
  'Card is untradeable',
  'Already own this',
  'Bad call',
  'Other',
];

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

const MODEL_OPTIONS = [
  { id: 'haiku', label: 'Claude Haiku' },
  { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { id: 'kimi-k2-6', label: 'Kimi K2.6' },
  { id: 'qwen3-80b', label: 'Qwen3 80B' },
  { id: 'mistral-small', label: 'Mistral Small' },
  { id: 'gpt-oss-120b', label: 'GPT OSS 120B' },
];

const OUTCOME_LABELS: Record<string, string> = {
  correct: 'Correct',
  incorrect: 'Incorrect',
  neutral: 'Neutral',
  expired: 'Expired',
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

  const [dismissMenuFor, setDismissMenuFor] = useState<number | null>(null);

  async function handleDismiss(rec: Recommendation, reason: string) {
    setDismissMenuFor(null);
    try {
      await (window as any).fcdb.dismissRecommendation({ id: rec.id, reason });
      setRecs(prev => prev.filter(r => r.id !== rec.id));
      if (reason === 'Wrong price data' && rec.card_id) {
        // Queue an immediate re-scrape so the bad price gets replaced
        const res = await (window as any).fcdb.requestFreshPrice({
          card_id: rec.card_id, platform: rec.platform,
        });
        if (res?.status === 'queued') {
          showToast({ message: `Fresh price re-scrape queued for ${rec.card_name}`, type: 'info' });
        } else if (res?.error) {
          showToast({ message: `Could not queue re-scrape: ${res.error}`, type: 'error' });
        }
      }
    } catch {}
  }

  const isFreeProvider = providerId !== 'haiku';
  const budgetExhausted = !isFreeProvider && budget !== null && !budget.can_generate;
  const visible = showDismissed ? recs : recs.filter(r => !r.dismissed_at);

  return (
    <div className="rec-view">
      {/* Header */}
      <div className="rec-header">
        <h2>Recommendations</h2>
        <div className="spacer" />
        <label className="rec-check">
          <input type="checkbox" checked={showDismissed} onChange={e => setShowDismissed(e.target.checked)} />
          Show dismissed
        </label>
        <label className="rec-check">
          <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
          Show all history
        </label>
        <label className="rec-check">
          Model:
          <select
            className="rec-model-select"
            value={providerId}
            onChange={e => setProviderId(e.target.value)}
          >
            {MODEL_OPTIONS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        </label>
        <div title={budgetExhausted ? `Daily AI budget used ($${budget?.cap_usd.toFixed(2)}). Resets at midnight UTC.` : undefined}>
          <button
            className="btn"
            onClick={handleRefresh}
            disabled={generating || budgetExhausted}
          >
            {generating ? 'Generating…' : budgetExhausted ? 'Budget used' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div className={`rec-toast rec-toast-${toast.type}`}>
          <span>{toast.message}</span>
          <button className="rec-toast-close" onClick={() => setToast(null)}>✕</button>
        </div>
      )}

      {/* Stats bar */}
      {stats && (
        <div className="rec-stats">
          <span>Last 7 days: <strong>{stats.total_evaluated}</strong> evaluated</span>
          {stats.accuracy_pct !== null && (
            <span>Accuracy: <strong className="rise">{stats.accuracy_pct.toFixed(0)}%</strong></span>
          )}
          <span>Buys: <strong className="rise">{stats.buy_total}</strong></span>
          <span>Avoids: <strong className="fall">{stats.avoid_total}</strong></span>
          {stats.next_eval_in_hours !== null && (
            <span className="push-right">
              Next eval: <strong>in {stats.next_eval_in_hours}h</strong>
            </span>
          )}
          {stats.oldest_pending_hours !== null && (
            <span>Oldest pending: <strong>{stats.oldest_pending_hours}h old</strong></span>
          )}
        </div>
      )}

      {/* Budget bar */}
      {budget && (
        <div className="rec-budget">
          {isFreeProvider ? (
            <span>
              Model: <strong className="rise">
                {MODEL_OPTIONS.find(m => m.id === providerId)?.label || providerId}
              </strong>{' · '}
              <strong className="rise">Free (NVIDIA · 40 RPM)</strong>
            </span>
          ) : (
            <>
              <span>Daily AI budget: <strong className={budgetExhausted ? 'fall' : ''}>
                ${budget.spent_today_usd.toFixed(4)} / ${budget.cap_usd.toFixed(2)}
              </strong></span>
              <span>Remaining: <strong className={budgetExhausted ? 'fall' : 'rise'}>
                ${budget.remaining_usd.toFixed(4)}
              </strong></span>
            </>
          )}
          {!isFreeProvider && budgetExhausted && (
            <span>Resets at midnight UTC</span>
          )}
        </div>
      )}

      {error && (
        <div className="rec-error">
          {error}
        </div>
      )}

      {visible.length === 0 && !generating && (
        <div className="rec-empty">
          No recommendations yet.{' '}
          {!budgetExhausted && (
            <button className="rec-empty-link" onClick={handleRefresh}>
              Generate now
            </button>
          )}
        </div>
      )}

      {/* Recommendation cards */}
      <div className="rec-list">
        {visible.map(rec => {
          const dismissed = !!rec.dismissed_at;
          const callClass = rec.call === 'buy' ? 'rec-call-buy' : rec.call === 'avoid' ? 'rec-call-avoid' : 'rec-call-hold';
          return (
            <div key={rec.id} className={`rec-card${dismissed ? ' dismissed' : ''}`}>
              {/* Top row */}
              <div className="rec-card-top">
                <span className={`rec-call ${callClass}`}>{rec.call}</span>
                <span className="rec-confidence">{rec.confidence.toFixed(0)}% confidence</span>
                <span className="rec-meta-sep">·</span>
                <span className="rec-meta">{horizonLabel(rec.horizon_hours)}</span>
                <div className="spacer" style={{ flex: 1 }} />
                <span className="rec-time">{timeAgo(rec.ts_utc)}</span>
              </div>

              {/* Card name + prior count */}
              <div className="rec-card-name">
                <span>
                  {dismissed
                    ? <s>{rec.card_name} — {rec.version_name}</s>
                    : `${rec.card_name} — ${rec.version_name}`}
                </span>
                <span className="rec-platform">{rec.platform.toUpperCase()}</span>
                {rec.prior_count > 1 && (
                  <span className="rec-prior">{rec.prior_count - 1} previous</span>
                )}
                {rec.model_id && (() => {
                  const mi = getModelDisplay(rec.model_id);
                  return (
                    <span className={`rec-model-badge ${mi.isNvidia ? 'rec-model-nvidia' : 'rec-model-anthropic'}`}>
                      {mi.label}
                    </span>
                  );
                })()}
              </div>

              {/* Prices */}
              {rec.target_price && (
                <div className="rec-price">
                  {rec.call === 'buy' ? 'Buy at: ' : 'Target: '}
                  <strong>{rec.target_price.toLocaleString()} coins</strong>
                </div>
              )}

              {/* Reasoning */}
              {rec.reasoning && (
                <div className="rec-reasoning">{rec.reasoning}</div>
              )}

              {/* Footer */}
              <div className="rec-card-footer">
                {!dismissed && (
                  <div className="rec-dismiss-wrap">
                    <button
                      className="rec-dismiss-btn"
                      onClick={() => setDismissMenuFor(dismissMenuFor === rec.id ? null : rec.id)}
                    >
                      Dismiss {dismissMenuFor === rec.id ? '▴' : '▾'}
                    </button>
                    {dismissMenuFor === rec.id && (
                      <div className="rec-dismiss-menu">
                        {DISMISS_REASONS.map(reason => (
                          <button
                            key={reason}
                            className="rec-dismiss-item"
                            onClick={() => handleDismiss(rec, reason)}
                          >
                            {reason}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {dismissed && rec.dismissed_reason && (
                  <span className="rec-dismissed-reason">Dismissed: {rec.dismissed_reason}</span>
                )}
                <div className="spacer" />
                {rec.outcome_verdict ? (
                  <span className={`rec-outcome rec-outcome-${rec.outcome_verdict}`}>
                    {OUTCOME_LABELS[rec.outcome_verdict] || rec.outcome_verdict}
                  </span>
                ) : (
                  <span className="rec-outcome rec-outcome-pending">Pending</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
