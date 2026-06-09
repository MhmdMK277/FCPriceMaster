import { useState, useEffect, useCallback, useRef } from 'react';
import type { Platform, AskResult, AskVerdict, LLMHistoryRow, MultiModelResult, MultiModelVerdict, ProviderAvailability } from '../lib/types';

const NVIDIA_PROVIDERS = [
  { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro' },
  { id: 'kimi-k2-6',       name: 'Kimi K2.6' },
  { id: 'qwen3-80b',       name: 'Qwen3 80B' },
  { id: 'mistral-small',   name: 'Mistral Small' },
  { id: 'gpt-oss-120b',    name: 'GPT OSS 120B' },
  { id: 'mistral-vision',  name: 'Mistral Vision', image: true },
];

function VerdictBadge({ verdict }: { verdict: string | null | undefined }) {
  if (!verdict) return null;
  const cls = verdict === 'buy' ? 'verdict-buy' : verdict === 'hold' ? 'verdict-hold' : 'verdict-avoid';
  return <span className={`verdict-badge ${cls}`}>{verdict.toUpperCase()}</span>;
}

function RiskBadge({ risk }: { risk: string }) {
  const cls = risk === 'low' ? 'risk-low' : risk === 'high' ? 'risk-high' : 'risk-medium';
  return <span className={`risk-badge ${cls}`}>{risk}</span>;
}

function ProviderBadge({ isNvidia }: { isNvidia: boolean }) {
  return (
    <span className={`provider-badge ${isNvidia ? 'provider-nvidia' : 'provider-anthropic'}`}>
      {isNvidia ? 'NVIDIA' : 'Anthropic'}
    </span>
  );
}

function fmt(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function SingleResult({ result }: { result: AskResult }) {
  return (
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
            <span className="ask-ctx-pill">Cards: {result.context_used.cards.join(', ')}</span>
          )}
          {result.context_used.signals_count > 0 && (
            <span className="ask-ctx-pill">{result.context_used.signals_count} signals used</span>
          )}
        </div>
      )}
    </div>
  );
}

function MultiVerdictCard({ v }: { v: MultiModelVerdict }) {
  const [open, setOpen] = useState(false);
  const isNvidia = v.provider_id !== 'haiku';

  if (v.error) {
    return (
      <div className="multi-verdict-card multi-verdict-error">
        <div className="multi-verdict-header">
          <ProviderBadge isNvidia={isNvidia} />
          <span className="multi-verdict-name">{v.provider_name}</span>
        </div>
        <p className="multi-verdict-err-text">{v.error}</p>
      </div>
    );
  }

  return (
    <div className={`multi-verdict-card multi-verdict-${v.action}`}>
      <div className="multi-verdict-header">
        <ProviderBadge isNvidia={isNvidia} />
        <span className="multi-verdict-name">{v.provider_name}</span>
        <VerdictBadge verdict={v.action} />
        <span className="ask-confidence">{v.confidence}%</span>
        <RiskBadge risk={v.risk} />
      </div>
      <div className="multi-verdict-reasoning">
        <p className={open ? '' : 'multi-verdict-clamp'}>{v.reasoning}</p>
        {v.reasoning.length > 120 && (
          <button className="multi-verdict-expand" onClick={() => setOpen(o => !o)}>
            {open ? 'Less' : 'More'}
          </button>
        )}
      </div>
      <div className="multi-verdict-footer">
        <span className="multi-verdict-horizon">{v.horizon}</span>
        <span className="multi-verdict-cost">
          {v.cost_usd > 0 ? `$${v.cost_usd.toFixed(4)}` : '$0.00'}
        </span>
      </div>
    </div>
  );
}

function MultiResult({ result }: { result: MultiModelResult }) {
  const successVerdicts = result.verdicts.filter(v => !v.error);
  const actions = [...new Set(successVerdicts.map(v => v.action))];
  const disagree = actions.length > 1;

  return (
    <div className="multi-result">
      {disagree && (
        <div className="multi-disagree-banner">
          ⚠ Models disagree — {successVerdicts.map(v => `${v.provider_name}: ${v.action.toUpperCase()}`).join(' · ')}
        </div>
      )}
      {result.context_used.cards.length > 0 && (
        <div className="ask-context-used">
          <span className="ask-ctx-pill">Cards: {result.context_used.cards.join(', ')}</span>
          {result.context_used.signals_count > 0 && (
            <span className="ask-ctx-pill">{result.context_used.signals_count} signals used</span>
          )}
        </div>
      )}
      <div className="multi-verdict-grid">
        {result.verdicts.map(v => <MultiVerdictCard key={v.provider_id} v={v} />)}
      </div>
    </div>
  );
}

export function Ask({ platform, setPlatform }: { platform: Platform; setPlatform: (p: Platform) => void }) {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [singleResult, setSingleResult] = useState<AskResult | null>(null);
  const [multiResult, setMultiResult] = useState<MultiModelResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<LLMHistoryRow[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [dailyStats, setDailyStats] = useState<{ calls: number; cost_usd: number } | null>(null);
  const [providerAvail, setProviderAvail] = useState<ProviderAvailability>({ haiku: true, nvidia: false });
  const [selectedProviders, setSelectedProviders] = useState<string[]>(['haiku']);
  const [imageB64, setImageB64] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [imageNote, setImageNote] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadHistory = useCallback(async () => {
    const rows = await window.fcdb.getLLMHistory({ limit: 10 });
    setHistory(rows);
    if (rows.length > 0) {
      const totalCost = rows.reduce((s: number, r: LLMHistoryRow) => s + (r.cost_usd || 0), 0);
      setDailyStats({ calls: rows.length, cost_usd: totalCost });
    }
  }, []);

  useEffect(() => {
    loadHistory();
    window.fcdb.getProviderAvailability().then((avail: ProviderAvailability) => {
      setProviderAvail(avail);
    }).catch(() => {});
  }, [loadHistory]);

  function toggleProvider(id: string) {
    setSelectedProviders(prev =>
      prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id]
    );
  }

  function handleImageSelected(file: File | null) {
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type)) {
      setError('Attach a PNG or JPG image');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || '');
      setImageB64(dataUrl.split(',', 2)[1] || null);
      setImagePreview(dataUrl);
      setImageNote(null);
    };
    reader.readAsDataURL(file);
  }

  function clearImage() {
    setImageB64(null);
    setImagePreview(null);
    setImageNote(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  async function handleAnalyse() {
    if (!text.trim() && !imageB64) return;
    let providers = selectedProviders.filter(id => id === 'haiku' ? providerAvail.haiku : providerAvail.nvidia);
    if (imageB64 && providerAvail.nvidia && !providers.includes('mistral-vision')) {
      providers = [...providers, 'mistral-vision'];
      setSelectedProviders(prev => prev.includes('mistral-vision') ? prev : [...prev, 'mistral-vision']);
      setImageNote('Mistral Vision auto-selected for image analysis');
    }
    if (providers.length === 0) {
      setError('Select at least one model');
      return;
    }
    setLoading(true);
    setError(null);
    setSingleResult(null);
    setMultiResult(null);

    try {
      const res = await window.fcdb.askMultiModel({
        trade_call: text.trim() || 'Image analysis',
        provider_ids: providers,
        platform,
        image_b64: imageB64,
      });
      if (res.error) {
        setError(res.error);
      } else {
        setMultiResult(res);
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
        <div className="ask-attach-row">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg"
            style={{ display: 'none' }}
            onChange={e => handleImageSelected(e.target.files?.[0] || null)}
          />
          <button className="attach-btn" onClick={() => fileInputRef.current?.click()} type="button">
            Image
          </button>
          {imagePreview && (
            <button className="attach-clear" onClick={clearImage} type="button">
              Remove
            </button>
          )}
          {imageNote && <span className="ask-image-note">{imageNote}</span>}
        </div>
        {imagePreview && (
          <div className="ask-image-preview">
            <img src={imagePreview} alt="Attached preview" />
          </div>
        )}

        <div className="ask-provider-row">
          <span className="ask-label">Models:</span>
          <label
            className={`provider-checkbox ${!providerAvail.haiku ? 'provider-checkbox-disabled' : ''}`}
            title={providerAvail.haiku ? '' : 'No ANTHROPIC_API_KEY set in .env'}
          >
            <input
              type="checkbox"
              checked={selectedProviders.includes('haiku') && providerAvail.haiku}
              disabled={!providerAvail.haiku}
              onChange={() => toggleProvider('haiku')}
            />
            <span className={`provider-chip ${providerAvail.haiku ? 'provider-chip-anthropic' : 'provider-chip-nokey'}`}>
              Claude Haiku{!providerAvail.haiku ? ' (No key)' : ''}
            </span>
          </label>
          {NVIDIA_PROVIDERS.map(p => {
            const available = providerAvail.nvidia;
            const checked = selectedProviders.includes(p.id);
            return (
              <label
                key={p.id}
                className={`provider-checkbox ${!available ? 'provider-checkbox-disabled' : ''}`}
                title={available ? '' : 'No NVIDIA_API_KEY set in .env'}
              >
                <input
                  type="checkbox"
                  checked={checked && available}
                  disabled={!available}
                  onChange={() => toggleProvider(p.id)}
                />
                <span className={`provider-chip ${available ? 'provider-chip-nvidia' : 'provider-chip-nokey'}`}>
                  {p.name}{p.image ? ' (image)' : ''}{!available ? ' (No key)' : ''}
                </span>
              </label>
            );
          })}
        </div>

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
            disabled={loading || (!text.trim() && !imageB64)}
          >
            {loading ? 'Thinking…' : selectedProviders.length > 1 ? `Analyse (${selectedProviders.length} models)` : 'Analyse'}
          </button>
        </div>
      </div>

      {error && <div className="ask-error">{error}</div>}

      {loading && (
        <div className="ask-loading">
          <span className="ask-spinner" />
          {selectedProviders.length > 1 ? `Querying ${selectedProviders.length} models in parallel…` : 'Analysing trade call…'}
        </div>
      )}

      {singleResult && !loading && <SingleResult result={singleResult} />}
      {multiResult && !loading && <MultiResult result={multiResult} />}

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
          {history.map((row: LLMHistoryRow) => {
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
