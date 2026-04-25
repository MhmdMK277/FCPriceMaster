import { useState, useEffect, useCallback } from 'react';
import type { Platform, AskResult, AskVerdict, LLMHistoryRow } from '../lib/types';

function VerdictBadge({ verdict }: { verdict: string | null | undefined }) {
  if (!verdict) return null;
  const cls = verdict === 'buy' ? 'verdict-buy' : verdict === 'hold' ? 'verdict-hold' : 'verdict-avoid';
  return <span className={`verdict-badge ${cls}`}>{verdict.toUpperCase()}</span>;
}

function RiskBadge({ risk }: { risk: string }) {
  const cls = risk === 'low' ? 'risk-low' : risk === 'high' ? 'risk-high' : 'risk-medium';
  return <span className={`risk-badge ${cls}`}>{risk}</span>;
}

function fmt(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function Ask({ platform, setPlatform }: { platform: Platform; setPlatform: (p: Platform) => void }) {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<LLMHistoryRow[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [dailyStats, setDailyStats] = useState<{ calls: number; cost_usd: number } | null>(null);

  const loadHistory = useCallback(async () => {
    const rows = await window.fcdb.getLLMHistory({ limit: 10 });
    setHistory(rows);
    if (rows.length > 0) {
      const totalCost = rows.reduce((s, r) => s + (r.cost_usd || 0), 0);
      setDailyStats({ calls: rows.length, cost_usd: totalCost });
    }
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  async function handleAnalyse() {
    if (!text.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await window.fcdb.askLLM({ text: text.trim(), platform });
      if (res.error) {
        setError(res.error);
      } else {
        setResult(res);
        loadHistory();
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="view">
      <div className="view-header">
        <h2>Ask</h2>
        <span className="ask-subtitle">AI trade-call analysis</span>
      </div>

      <div className="ask-input-area">
        <textarea
          className="ask-textarea"
          placeholder="Paste a trade call, tweet, or Discord message…"
          value={text}
          onChange={e => setText(e.target.value)}
          rows={4}
        />

        <div className="ask-controls">
          <div className="ask-platform-row">
            <span className="ask-label">Platform:</span>
            <button
              className={`plat-btn ${platform === 'pc' ? 'active' : ''}`}
              onClick={() => setPlatform('pc')}
            >PC</button>
            <button
              className={`plat-btn ${platform === 'console' ? 'active' : ''}`}
              onClick={() => setPlatform('console')}
            >Console</button>
          </div>
          <button
            className="btn ask-btn"
            onClick={handleAnalyse}
            disabled={loading || !text.trim()}
          >
            {loading ? 'Thinking…' : 'Analyse'}
          </button>
        </div>
      </div>

      {error && (
        <div className="ask-error">{error}</div>
      )}

      {loading && (
        <div className="ask-loading">
          <span className="ask-spinner" />
          Analysing trade call…
        </div>
      )}

      {result && !loading && (
        <div className="ask-result">
          <div className="ask-result-header">
            <VerdictBadge verdict={result.verdict.verdict} />
            <span className="ask-confidence">{result.verdict.confidence}% confidence</span>
            <RiskBadge risk={result.verdict.risk} />
            <span className="ask-horizon">{result.verdict.horizon}</span>
          </div>

          <p className="ask-reasoning">{result.verdict.reasoning}</p>
          <p className="ask-price-context">{result.verdict.price_context}</p>

          {(result.verdict.suggested_buy_price || result.verdict.suggested_sell_price) && (
            <div className="ask-prices">
              {result.verdict.suggested_buy_price && (
                <span className="ask-price-item">
                  <span className="ask-price-label">Buy at:</span>
                  <span className="ask-price-value">{fmt(result.verdict.suggested_buy_price)}</span>
                </span>
              )}
              {result.verdict.suggested_sell_price && (
                <span className="ask-price-item">
                  <span className="ask-price-label">Sell at:</span>
                  <span className="ask-price-value">{fmt(result.verdict.suggested_sell_price)}</span>
                </span>
              )}
            </div>
          )}

          {result.context_used && (
            <div className="ask-context-used">
              {result.context_used.cards.length > 0 && (
                <span className="ask-ctx-pill">
                  Cards: {result.context_used.cards.join(', ')}
                </span>
              )}
              {result.context_used.signals_count > 0 && (
                <span className="ask-ctx-pill">
                  {result.context_used.signals_count} signals used
                </span>
              )}
            </div>
          )}
        </div>
      )}

      <div className="ask-footer">
        {dailyStats && (
          <span className="ask-usage">
            Today: {dailyStats.calls} call{dailyStats.calls !== 1 ? 's' : ''}, ~${dailyStats.cost_usd.toFixed(4)} used
          </span>
        )}
        <button
          className="ask-history-toggle"
          onClick={() => setHistoryOpen(o => !o)}
        >
          {historyOpen ? 'Hide history' : `History (${history.length})`}
        </button>
      </div>

      {historyOpen && history.length > 0 && (
        <div className="ask-history">
          {history.map(row => {
            let parsedVerdict: AskVerdict | null = null;
            let verdictString: string | null = null;
            try {
              const parsed = JSON.parse(row.output_json || '{}') as AskResult;
              parsedVerdict = parsed?.verdict ?? null;
              verdictString = parsed?.verdict?.verdict ?? null;
            } catch {}
            return (
              <div key={row.id} className="ask-history-row">
                <div className="ask-history-meta">
                  <span className="age">{new Date(row.ts_utc).toLocaleString()}</span>
                  {verdictString && <VerdictBadge verdict={verdictString} />}
                  <span className="ask-history-cost">${(row.cost_usd || 0).toFixed(4)}</span>
                </div>
                <div className="ask-history-text">{row.input_text}</div>
                {parsedVerdict && <div className="ask-history-reasoning">{parsedVerdict.reasoning}</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
