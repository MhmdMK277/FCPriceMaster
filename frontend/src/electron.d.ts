import type {
  Platform, TopMoverRow, CardSearchRow, CardDetailResult,
  ScraperHealthRow, AppSettings, SignalRow,
  FodderSummaryRow, FodderSnapshotRow, AskResult, LLMHistoryRow,
} from './lib/types';

declare global {
  interface Window {
    fcdb: {
      getTopMovers(opts: { platform: Platform; hoursBack?: number; limit?: number }): Promise<TopMoverRow[]>;
      searchCards(opts: { query: string; limit?: number }): Promise<CardSearchRow[]>;
      getCardDetail(opts: { cardKey: string; platform: Platform }): Promise<CardDetailResult | null>;
      getScraperHealth(opts?: { limit?: number }): Promise<ScraperHealthRow[]>;
      getRecentSignals(opts?: { limit?: number; hoursBack?: number; sourceFilter?: string }): Promise<SignalRow[]>;
      getFodderSummary(opts: { platform: Platform }): Promise<FodderSummaryRow[]>;
      getFodderSnapshot(opts: { rating: number; platform: Platform; hoursBack?: number }): Promise<FodderSnapshotRow[]>;
      getLLMHistory(opts?: { limit?: number }): Promise<LLMHistoryRow[]>;
      askLLM(opts: { text: string; platform: Platform }): Promise<AskResult>;
      getSettings(): Promise<AppSettings>;
      setSetting(key: string, value: unknown): Promise<AppSettings>;
      restartBackend(): Promise<void>;
      stopBackend(): Promise<void>;
      backendRunning(): Promise<boolean>;
    };
  }
}
