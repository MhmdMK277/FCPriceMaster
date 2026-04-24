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
}
