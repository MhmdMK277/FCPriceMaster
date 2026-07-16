import { useState, useEffect, useCallback, useRef } from 'react';
import type { Platform, LLMHistoryRow, MultiModelVerdict, ProviderAvailability } from '../lib/types';

const TEXT_PROVIDER_LIST = [
  { id: 'haiku',           name: 'Claude Haiku',    isNvidia: false },
  { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', isNvidia: true  },
  { id: 'kimi-k2-6',       name: 'Kimi K2.6',       isNvidia: true  },
  { id: 'qwen3-80b',       name: 'Qwen3 80B',        isNvidia: true  },
  { id: 'mistral-small',   name: 'Mistral Small',    isNvidia: true  },
  { id: 'gpt-oss-120b',    name: 'GPT OSS 120B',    isNvidia: true  },
];
const VISION_PROVIDER = { id: 'mistral-vision', name: 'Mistral Vision', isNvidia: true, image: true };
const ALL_DISPLAY_PROVIDERS = [...TEXT_PROVIDER_LIST, VISION_PROVIDER];

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

function VerdictBadge({ verdict }: { verdict: string | null | undefined }) {
  if (!verdict) return null;
  const v = verdict.toLowerCase();
  const cls = v === 'buy' ? 'verdict-buy' : v === 'hold' ? 'verdict-hold' : 'verdict-avoid';
  return <span className={`verdict-badge ${cls}`}>{v.toUpperCase()}</span>;
}

function ProviderBadge({ isNvidia }: { isNvidia: boolean }) {
  return (
    <span className={`provider-badge ${isNvidia ? 'provider-badge-nvidia' : 'provider-badge-anthropic'}`}>
      {isNvidia ? 'NVIDIA' : 'Anthropic'}
    </span>
  );
}

function MultiVerdictCard({ v }: { v: MultiModelVerdict }) {
  const [open, setOpen] = useState(false);
  const isNvidia = v.provider_id !== 'haiku';
  const PREVIEW = 120;
  const reasoning = v.reasoning || '';

  if (v.error) {
    return (
      <div className="multi-verdict-card multi-verdict-card-error">
        <div className="multi-verdict-header">
          <ProviderBadge isNvidia={isNvidia} />
          <span className="multi-verdict-name">{v.provider_name}</span>
        </div>
        <p className="multi-verdict-err-text">{(v.error || '').slice(0, 140)}</p>
      </div>
    );
  }

  return (
    <div className="multi-verdict-card">
      <div className="multi-verdict-header">
        <ProviderBadge isNvidia={isNvidia} />
        <span className="multi-verdict-name">{v.provider_name}</span>
        <VerdictBadge verdict={v.action} />
      </div>
      <div className="multi-verdict-confidence">
        <span className="multi-verdict-conf-num">{v.confidence}%</span>
        <span className="multi-verdict-meta">
          {v.elapsed_ms != null ? formatElapsed(v.elapsed_ms) : ''}
          {v.cost_usd > 0 ? ` · $${v.cost_usd.toFixed(4)}` : ' · free'}
        </span>
      </div>
      <div className="multi-verdict-reasoning">
        <p>{open || reasoning.length <= PREVIEW ? reasoning : reasoning.slice(0, PREVIEW) + '…'}</p>
        {reasoning.length > PREVIEW && (
          <button className="multi-verdict-expand" onClick={() => setOpen(o => !o)}>
            {open ? 'Show less' : 'Show more'}
          </button>
        )}
      </div>
      <div className="multi-verdict-footer">
        <span className="multi-verdict-horizon">{v.horizon}</span>
      </div>
    </div>
  );
}

function PendingCard({ name, isNvidia, hint }: { name: string; isNvidia: boolean; hint?: string }) {
  return (
    <div className="multi-verdict-card multi-verdict-card-pending">
      <div className="multi-verdict-header">
        <ProviderBadge isNvidia={isNvidia} />
        <span className="multi-verdict-name">{name}</span>
        <span className="ask-spinner" style={{ marginLeft: 8 }} />
      </div>
      <p className="multi-verdict-pending-text">Querying…</p>
      {hint && (
        <p className="multi-verdict-pending-hint">{hint}</p>
      )}
    </div>
  );
}

function CancelledCard({ name, isNvidia }: { name: string; isNvidia: boolean }) {
  return (
    <div className="multi-verdict-card multi-verdict-card-cancelled">
      <div className="multi-verdict-header">
        <ProviderBadge isNvidia={isNvidia} />
        <span className="multi-verdict-name">{name}</span>
        <span className="ask-ctx-pill">cancelled</span>
      </div>
    </div>
  );
}

function HistoryRow({
  row,
  expanded,
  onToggle,
}: {
  row: LLMHistoryRow;
  expanded: boolean;
  onToggle: () => void;
}) {

  if (row.feature === 'ask_multi') {
    let verdicts: MultiModelVerdict[] = [];
    try {
      const parsed = JSON.parse(row.output_json || '{}') as { verdicts?: MultiModelVerdict[] };
      verdicts = parsed.verdicts || [];
    } catch { /**/ }
    const buyCount   = verdicts.filter(v => v.action === 'buy').length;
    const holdCount  = verdicts.filter(v => v.action === 'hold').length;
    const avoidCount = verdicts.filter(v => v.action === 'avoid').length;
    const errCount   = verdicts.filter(v => !!v.error).length;

    return (
      <div className="ask-history-row" style={{ cursor: 'pointer' }} onClick={onToggle}>
        <div className="ask-history-meta">
          <span className="age">{new Date(row.ts_utc).toLocaleString()}</span>
          <span className="ask-history-count">{verdicts.length} models</span>
          {buyCount   > 0 && <span className="ask-history-badge risk-low">{buyCount}× BUY</span>}
          {holdCount  > 0 && <span className="ask-history-badge risk-medium">{holdCount}× HOLD</span>}
          {avoidCount > 0 && <span className="ask-history-badge risk-high">{avoidCount}× AVOID</span>}
          {errCount   > 0 && <span className="ask-history-count">{errCount} err</span>}
        </div>
        <div className="ask-history-text">
          {(row.input_text || '').slice(0, 100)}{(row.input_text || '').length > 100 ? '…' : ''}
        </div>
        {expanded && verdicts.length > 0 && (
          <div
            className="multi-verdict-grid"
            style={{ marginTop: 10 }}
            onClick={e => e.stopPropagation()}
          >
            {verdicts.map(v => <MultiVerdictCard key={v.provider_id} v={v} />)}
          </div>
        )}
      </div>
    );
  }

  // Legacy single-model row (feature='ask')
  let verdictStr: string | null = null;
  try {
    const p = JSON.parse(row.output_json || '{}') as { verdict?: string };
    verdictStr = p.verdict || null;
  } catch { /**/ }

  return (
    <div className="ask-history-row">
      <div className="ask-history-meta">
        <span className="age">{new Date(row.ts_utc).toLocaleString()}</span>
        {verdictStr && <VerdictBadge verdict={verdictStr} />}
        <span className="ask-history-cost">${(row.cost_usd || 0).toFixed(4)}</span>
      </div>
      <div className="ask-history-text">{row.input_text}</div>
    </div>
  );
}

type VerdictState = MultiModelVerdict | 'pending' | 'cancelled';

export function Ask({ platform, setPlatform }: { platform: Platform; setPlatform: (p: Platform) => void }) {
  const isElectron = typeof window !== 'undefined' && !!window.fcdb;

  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [verdictsByProvider, setVerdictsByProvider] = useState<Record<string, VerdictState>>({});
  const [contextInfo, setContextInfo] = useState<{ cards: string[]; signals_count: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<LLMHistoryRow[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyExpanded, setHistoryExpanded] = useState<Set<number>>(new Set());
  const [dailyStats, setDailyStats] = useState<{ calls: number; cost_usd: number } | null>(null);
  const [providerAvail, setProviderAvail] = useState<ProviderAvailability>({ haiku: true, nvidia: true });
  const [selectedProviders, setSelectedProviders] = useState<string[]>(['haiku']);
  const [imageB64, setImageB64] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [imageFileName, setImageFileName] = useState<string | null>(null);
  const [imageNote, setImageNote] = useState<string | null>(null);
  const [coldStartHints, setColdStartHints] = useState<Record<string, string>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  // Cold-start hints pushed from main while a provider fetch is still pending
  useEffect(() => {
    if (!isElectron || typeof window.fcdb.onProviderStatus !== 'function') return;
    const unsubscribe = window.fcdb.onProviderStatus((data) => {
      if (data.status === 'cold_starting' && data.session_id === sessionIdRef.current) {
        setColdStartHints(prev => ({ ...prev, [data.provider_id]: data.message }));
      }
    });
    return unsubscribe;
  }, [isElectron]);

  const loadHistory = useCallback(async () => {
    if (!isElectron) return;
    try {
      const rows = await window.fcdb.getLLMHistory({ limit: 30 });
      setHistory(rows);
      const totalCost = rows.reduce((s: number, r: LLMHistoryRow) => s + (r.cost_usd || 0), 0);
      if (rows.length > 0) setDailyStats({ calls: rows.length, cost_usd: totalCost });
    } catch { /**/ }
  }, [isElectron]);

  useEffect(() => {
    if (!isElectron) {
      setProviderAvail({ haiku: true, nvidia: true });
      return;
    }
    loadHistory();
    window.fcdb.getProviderAvailability().then((avail: ProviderAvailability) => {
      setProviderAvail(avail);
      if (!avail.haiku && avail.nvidia) setSelectedProviders(['deepseek-v4-pro']);
    }).catch(() => { /**/ });
  }, [loadHistory, isElectron]);

  function toggleProvider(id: string) {
    setSelectedProviders(prev =>
      prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id],
    );
  }

  function selectAll() {
    const available: string[] = [];
    if (providerAvail.haiku) available.push('haiku');
    if (providerAvail.nvidia) available.push('deepseek-v4-pro', 'kimi-k2-6', 'qwen3-80b', 'mistral-small', 'gpt-oss-120b');
    setSelectedProviders(available);
  }

  function clearAll() {
    setSelectedProviders([]);
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
      const b64 = dataUrl.split(',', 2)[1] || null;
      setImageB64(b64);
      setImagePreview(dataUrl);
      setImageFileName(file.name);
      if (providerAvail.nvidia) {
        setSelectedProviders(prev => prev.includes('mistral-vision') ? prev : [...prev, 'mistral-vision']);
        setImageNote('Auto-selected for image analysis');
      }
    };
    reader.readAsDataURL(file);
  }

  function clearImage() {
    setImageB64(null);
    setImagePreview(null);
    setImageFileName(null);
    setImageNote(null);
    setSelectedProviders(prev => prev.filter(p => p !== 'mistral-vision'));
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleCancel() {
    if (isElectron && sessionIdRef.current) {
      window.fcdb.cancelSession({ session_id: sessionIdRef.current }).catch(() => { /**/ });
    }
    if (abortRef.current) abortRef.current.abort();
    setVerdictsByProvider(prev => {
      const next = { ...prev };
      Object.keys(next).forEach(k => { if (next[k] === 'pending') next[k] = 'cancelled'; });
      return next;
    });
    setLoading(false);
    setError('Cancelled');
    setTimeout(() => setError(e => e === 'Cancelled' ? null : e), 1500);
  }

  async function handleAnalyse() {
    if (!text.trim() && !imageB64) return;
    if (!isElectron) { setError('Electron context required for live analysis'); return; }

    const providers = selectedProviders.filter(id => {
      if (id === 'haiku') return providerAvail.haiku;
      if (id === 'mistral-vision') return providerAvail.nvidia && !!imageB64;
      return providerAvail.nvidia;
    });
    if (providers.length === 0) { setError('Select at least one model'); return; }

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const sessionId = crypto.randomUUID();
    sessionIdRef.current = sessionId;
    setLoading(true);
    setError(null);
    setContextInfo(null);
    setColdStartHints({});

    const pending: Record<string, VerdictState> = {};
    providers.forEach(p => { pending[p] = 'pending'; });
    setVerdictsByProvider(pending);

    try {
      const { userMessage, context_info } = await window.fcdb.buildAskContext({ trade_call: text.trim() || 'Image analysis', platform });
      if (ctrl.signal.aborted) return;
      setContextInfo(context_info);

      const tradeCallText = text.trim() || 'Image analysis';
      const tasks = providers.map(async (providerId) => {
        try {
          const verdict = await window.fcdb.callSingleProvider({
            provider_id: providerId,
            user_message: userMessage,
            image_b64: imageB64,
            input_text: tradeCallText,
            session_id: sessionId,
          });
          if (!ctrl.signal.aborted) {
            if (verdict.error === 'cancelled') {
              setVerdictsByProvider(prev => ({ ...prev, [providerId]: 'cancelled' }));
            } else {
              setVerdictsByProvider(prev => ({ ...prev, [providerId]: verdict }));
            }
          }
          return verdict;
        } catch (e: unknown) {
          const errMsg = e instanceof Error ? e.message : String(e);
          const info = ALL_DISPLAY_PROVIDERS.find(p => p.id === providerId);
          const errVerdict: MultiModelVerdict = {
            provider_id: providerId,
            provider_name: info?.name || providerId,
            error: errMsg,
            action: 'hold', confidence: 0, reasoning: '', price_context: '', risk: 'medium',
            horizon: '', suggested_buy_price: null, suggested_sell_price: null, cost_usd: 0,
          };
          if (!ctrl.signal.aborted) {
            setVerdictsByProvider(prev => ({ ...prev, [providerId]: errVerdict }));
          }
          return errVerdict;
        }
      });

      const allVerdicts = await Promise.all(tasks);
      if (!ctrl.signal.aborted) {
        setLoading(false);
        window.fcdb.logAskMulti({ input_text: tradeCallText, verdicts: allVerdicts }).catch(() => { /**/ });
        loadHistory();
      }
    } catch (e: unknown) {
      if (!ctrl.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      }
    }
  }

  // Disagree banner
  const resolvedVerdicts = Object.values(verdictsByProvider)
    .filter((v): v is MultiModelVerdict => v !== 'pending' && v !== 'cancelled');
  const successVerdicts = resolvedVerdicts.filter(v => !v.error);
  const uniqueActions = [...new Set(successVerdicts.map(v => v.action))];
  const disagree = uniqueActions.length > 1;

  const hasVerdicts = Object.keys(verdictsByProvider).length > 0;

  const multiHistoryCount = history.filter(r => r.feature === 'ask_multi').length;

  return (
    <div className="view">
      <div className="view-header">
        <h2>Ask</h2>
        <span className="ask-subtitle">AI trade-call analysis</span>
        {!isElectron && (
          <span className="risk-badge risk-medium">
            dev mode — Electron required for live data
          </span>
        )}
      </div>

      <div className="ask-input-area">
        <textarea
          className="ask-textarea"
          placeholder="Paste a trade call, tweet, or Discord message…"
          value={text}
          onChange={e => setText(e.target.value)}
          rows={4}
        />

        {/* Image attach row */}
        <div className="ask-attach-row">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg"
            style={{ display: 'none' }}
            onChange={e => handleImageSelected(e.target.files?.[0] || null)}
          />
          <button className="attach-btn" onClick={() => fileInputRef.current?.click()} type="button">
            {imageFileName ? `📎 ${imageFileName}` : 'Image'}
          </button>
          {imagePreview && (
            <button className="attach-clear" onClick={clearImage} type="button">✕ Remove</button>
          )}
          {imageNote && <span className="ask-image-note">{imageNote}</span>}
        </div>
        {imagePreview && (
          <div className="ask-image-preview">
            <img src={imagePreview} alt="Attached" />
          </div>
        )}

        {/* Model selector */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <span className="ask-label">Models:</span>
            <button className="ask-toggle-link" onClick={selectAll} type="button">Select all</button>
            <span className="rec-meta-sep">·</span>
            <button className="ask-toggle-link" onClick={clearAll} type="button">Clear all</button>
          </div>
          <div className="ask-provider-row">
            {ALL_DISPLAY_PROVIDERS.map(p => {
              const isMistralVision = p.id === 'mistral-vision';
              const available = p.isNvidia ? providerAvail.nvidia : providerAvail.haiku;
              const disabled = !available || (isMistralVision && !imageB64);
              const checked = selectedProviders.includes(p.id) && !disabled;
              const title = !available
                ? `No ${p.isNvidia ? 'NVIDIA' : 'ANTHROPIC'}_API_KEY set in .env`
                : isMistralVision && !imageB64
                  ? 'Attach an image to enable Mistral Vision'
                  : '';
              return (
                <label
                  key={p.id}
                  className={`provider-checkbox ${disabled ? 'provider-checkbox-disabled' : ''}`}
                  title={title}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => { if (!isMistralVision) toggleProvider(p.id); }}
                  />
                  <span className={`provider-chip ${
                    !available ? 'provider-chip-nokey' : p.isNvidia ? 'provider-chip-nvidia' : 'provider-chip-anthropic'
                  }`}>
                    {p.name}
                    {!available ? ' (No key)' : ''}
                    {isMistralVision && !imageB64 ? ' (image only)' : ''}
                  </span>
                </label>
              );
            })}
          </div>
        </div>

        {/* Controls row */}
        <div className="ask-controls">
          <div className="ask-platform-row">
            <span className="ask-label">Platform:</span>
            <button className={`plat-btn ${platform === 'pc' ? 'active' : ''}`} onClick={() => setPlatform('pc')}>PC</button>
            <button className={`plat-btn ${platform === 'console' ? 'active' : ''}`} onClick={() => setPlatform('console')}>Console</button>
          </div>
          {loading ? (
            <button className="btn btn-danger ask-btn" onClick={handleCancel} type="button">Cancel</button>
          ) : (
            <button
              className="btn ask-btn"
              onClick={handleAnalyse}
              disabled={!text.trim() && !imageB64}
              type="button"
            >
              {selectedProviders.length > 1 ? `Analyse (${selectedProviders.length} models)` : 'Analyse'}
            </button>
          )}
        </div>
      </div>

      {error && <div className="ask-error">{error}</div>}

      {loading && (
        <div className="ask-loading">
          <span className="ask-spinner" />
          {`Querying ${Object.values(verdictsByProvider).filter(v => v === 'pending').length} model(s) in parallel…`}
        </div>
      )}

      {/* Verdict grid */}
      {hasVerdicts && (
        <div style={{ marginBottom: 16, maxWidth: 860 }}>
          {!loading && disagree && (
            <div className="multi-disagree-banner">
              ⚠ Models disagree — {successVerdicts.map(v => `${v.provider_name}: ${(v.action || '').toUpperCase()}`).join(' · ')}
            </div>
          )}
          {contextInfo && (contextInfo.cards.length > 0 || contextInfo.signals_count > 0) && (
            <div className="ask-context-used" style={{ marginBottom: 12 }}>
              {contextInfo.cards.length > 0 && (
                <span className="ask-ctx-pill">Cards: {contextInfo.cards.join(', ')}</span>
              )}
              {contextInfo.signals_count > 0 && (
                <span className="ask-ctx-pill">{contextInfo.signals_count} signals used</span>
              )}
            </div>
          )}
          <div className="multi-verdict-grid">
            {ALL_DISPLAY_PROVIDERS.filter(p => p.id in verdictsByProvider).map(p => {
              const state = verdictsByProvider[p.id];
              if (state === 'pending')   return <PendingCard   key={p.id} name={p.name} isNvidia={p.isNvidia} hint={coldStartHints[p.id]} />;
              if (state === 'cancelled') return <CancelledCard key={p.id} name={p.name} isNvidia={p.isNvidia} />;
              return <MultiVerdictCard key={p.id} v={state} />;
            })}
          </div>
        </div>
      )}

      <div className="ask-footer">
        {dailyStats && (
          <span className="ask-usage">
            Today: {dailyStats.calls} session{dailyStats.calls !== 1 ? 's' : ''}, ~${dailyStats.cost_usd.toFixed(4)} used
          </span>
        )}
        <button className="ask-history-toggle" onClick={() => setHistoryOpen(o => !o)} type="button">
          {historyOpen ? 'Hide history' : `History (${multiHistoryCount || history.length})`}
        </button>
      </div>

      {historyOpen && history.length > 0 && (
        <div className="ask-history">
          {history.map((row: LLMHistoryRow) => (
            <HistoryRow
              key={row.id}
              row={row}
              expanded={historyExpanded.has(row.id)}
              onToggle={() => setHistoryExpanded(prev => {
                const next = new Set(prev);
                next.has(row.id) ? next.delete(row.id) : next.add(row.id);
                return next;
              })}
            />
          ))}
        </div>
      )}
    </div>
  );
}
