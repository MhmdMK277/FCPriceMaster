export type Platform = 'pc' | 'console';

export interface TopMoverRow {
  id: number;
  card_key: string;
  player_name: string;
  version_name: string;
  current_price: number;
  price_24h_ago: number;
  price_change: number;
  pct_change: number;
  rating: string | null;
  position: string | null;
}

export interface CardSearchRow {
  id: number;
  card_key: string;
  player_name: string;
  version_name: string;
  game_edition: string;
  rating: string | null;
  position: string | null;
  league: string | null;
}

export interface CardAttr {
  key: string;
  value: string;
}

export interface Snapshot {
  ts_utc: string;
  bin_price: number | null;
  volume_proxy: number | null;
}

export interface CardRecord {
  id: number;
  card_key: string;
  player_name: string;
  version_name: string;
  game_edition: string;
  created_at_utc: string;
}

export interface CardDetailResult {
  card: CardRecord;
  attrs: CardAttr[];
  snapshots: Snapshot[];
}

export interface ScraperHealthRow {
  id: number;
  source: string;
  run_at_utc: string;
  success: number;
  records_written: number;
  consecutive_failures: number;
  last_error: string | null;
  schema_diff: string | null;
}

export interface AppSettings {
  autoStartBackend: boolean;
  enableDiscordIngest: boolean;
  enableTwitterIngest: boolean;
}

export interface SignalRow {
  id: number;
  source: string;
  source_server: string | null;
  signal_type: string;
  ts_utc: string;
  original_author: string | null;
  original_ts_utc: string | null;
  raw_text: string | null;
  has_attachments: number;
  attachment_urls: string[];
  signal_category: string;
  priority: string;
}

export interface FodderSummaryRow {
  rating: number;
  cheapest_bin: number | null;
  median_bin: number | null;
  last_updated: string | null;
  cheapest_bin_24h_ago: number | null;
}

export interface FodderSnapshotRow {
  rating: number;
  platform: string;
  ts_utc: string;
  cheapest_bin: number | null;
  second_cheapest_bin: number | null;
  median_bin: number | null;
}

export interface AskVerdict {
  verdict: 'buy' | 'hold' | 'avoid';
  confidence: number;
  reasoning: string;
  price_context: string;
  risk: 'low' | 'medium' | 'high';
  suggested_buy_price: number | null;
  suggested_sell_price: number | null;
  horizon: string;
}

export interface AskResult {
  verdict: AskVerdict;
  context_used: {
    cards: string[];
    fodder_ratings: number[];
    signals_count: number;
  };
  usage: {
    model: string;
    input_tokens: number;
    output_tokens: number;
  };
  error?: string;
}

export interface LLMHistoryRow {
  id: number;
  ts_utc: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  feature: string;
  input_text: string | null;
  output_json: string | null;
}
