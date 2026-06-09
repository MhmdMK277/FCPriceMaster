import { useState, useEffect, useCallback } from 'react';
import type { SignalRow } from '../lib/types';

const SOURCE_OPTIONS = [
  { label: 'All sources', value: '' },
  { label: 'Twitter',     value: 'twitter' },
  { label: 'Discord',     value: 'discord' },
  { label: 'Reddit',      value: 'reddit' },
  { label: 'EA News',     value: 'ea_news' },
  { label: 'source_1',    value: 'source_1' },
  { label: 'source_2',    value: 'source_2' },
  { label: 'source_3',    value: 'source_3' },
  { label: 'owner_direct', value: 'owner_direct' },
];

const TIME_OPTIONS = [
  { label: 'Last 1h',  hours: 1   },
  { label: 'Last 6h',  hours: 6   },
  { label: 'Last 24h', hours: 24  },
  { label: 'Last 7d',  hours: 168 },
];

const SOURCE_ICONS: Record<string, string> = {
  twitter:    '[TW]',
  discord:    '[DC]',
  reddit:     '[RD]',
  ea_news:    '[EA]',
};

const PRIORITY_COLORS: Record<string, string> = {
  high:   'sig-priority-high',
  medium: 'sig-priority-medium',
  low:    'sig-priority-low',
};

const CONTEXT_BADGES: Record<string, { label: string; cls: string }> = {
  fut_market: { label: 'FUT Market', cls: 'ctx-fut' },
  irl_transfer: { label: 'IRL Transfer', cls: 'ctx-transfer' },
  irl_result: { label: 'IRL Result', cls: 'ctx-result' },
  promo_leak: { label: 'Promo Leak', cls: 'ctx-promo' },
};

function relativeTime(isoUtc: string): string {
  const diffMs = Date.now() - new Date(isoUtc + (isoUtc.endsWith('Z') ? '' : 'Z')).getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1)  return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)  return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function Signals() {
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [sourceFilter, setSourceFilter] = useState('');
  const [hoursBack, setHoursBack] = useState(24);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const load = useCallback(async () => {
    const filter = sourceFilter || undefined;
    const data = await window.fcdb.getRecentSignals({ limit: 200, hoursBack, sourceFilter: filter });
    setRows(data);
    setLastRefresh(new Date());
  }, [sourceFilter, hoursBack]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [load]);

  function toggleExpand(id: number) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  return (
    <div className="view">
      <div className="view-header">
        <h2>Signals</h2>
        <span className="refresh-time">
          {lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : 'Loading…'}
        </span>
      </div>

      <div className="signals-toolbar">
        <select
          className="sig-select"
          value={sourceFilter}
          onChange={e => setSourceFilter(e.target.value)}
        >
          {SOURCE_OPTIONS.map(s => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>

        <div className="time-btns">
          {TIME_OPTIONS.map(opt => (
            <button
              key={opt.hours}
              className={`time-btn ${hoursBack === opt.hours ? 'active' : ''}`}
              onClick={() => setHoursBack(opt.hours)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="empty">
          No signals in the selected window.
        </div>
      ) : (
        <div className="signal-list">
          {rows.map(row => (
            <SignalCard
              key={row.id}
              row={row}
              isExpanded={expanded.has(row.id)}
              onToggle={() => toggleExpand(row.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SignalCard({
  row,
  isExpanded,
  onToggle,
}: {
  row: SignalRow;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const MAX_CHARS = 300;
  const text = row.raw_text || '';
  const isLong = text.length > MAX_CHARS;
  const displayText = isExpanded || !isLong ? text : text.slice(0, MAX_CHARS) + '…';
  const timeStr = row.original_ts_utc || row.ts_utc;
  const sourceIcon = SOURCE_ICONS[row.source] || `[${row.source.slice(0, 2).toUpperCase()}]`;
  const priorityClass = PRIORITY_COLORS[row.priority] || '';

  // For Twitter, show the handle (@source_server) prominently.
  const displayName = row.source === 'twitter'
    ? `@${row.source_server || 'unknown'}`
    : (row.source_server || row.source);

  return (
    <div className={`signal-card ${priorityClass}`}>
      <div className="signal-meta">
        <span className="signal-source-icon" title={row.source}>{sourceIcon}</span>
        <span className="signal-source">{displayName}</span>
        {row.original_author && row.source !== 'twitter' && (
          <span className="signal-author" title="Original author">{row.original_author}</span>
        )}
        <span className="signal-time" title={timeStr}>{relativeTime(timeStr)}</span>
        {row.has_attachments ? <span className="sig-badge">img</span> : null}
        {row.signal_context && (
          <span className={`sig-badge context ${CONTEXT_BADGES[row.signal_context]?.cls || 'ctx-fut'}`}>
            {CONTEXT_BADGES[row.signal_context]?.label || row.signal_context}
          </span>
        )}
        {row.signal_type === 'forward' && <span className="sig-badge fwd">fwd</span>}
        {row.signal_category && (
          <span className="sig-badge cat">{row.signal_category}</span>
        )}
        {row.priority === 'high' && (
          <span className="sig-badge high">high</span>
        )}
      </div>

      <div className="signal-text">
        {displayText || <em className="no-text">— no text —</em>}
      </div>

      {isLong && (
        <button className="expand-btn" onClick={onToggle}>
          {isExpanded ? 'Show less' : 'Show more'}
        </button>
      )}

      {isExpanded && row.attachment_urls && row.attachment_urls.length > 0 && (
        <div className="signal-thumbs">
          {row.attachment_urls.map((url, i) => (
            <a key={i} href={url} target="_blank" rel="noreferrer">
              <img className="thumb" src={url} alt="attachment" loading="lazy" />
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
