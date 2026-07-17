import type {
  Platform, TopMoverRow, CardSearchRow, CardDetailResult,
  ScraperHealthRow, AppSettings, SignalRow,
  FodderSummaryRow, FodderSnapshotRow, FodderCard, AskResult, LLMHistoryRow,
  MultiModelResult, ProviderAvailability,
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
      getFodderByRating(opts: { rating: number; platform: Platform; limit?: number }): Promise<FodderCard[]>;
      getFodderHistory(opts: { rating: number; platform: Platform; hoursBack?: number }): Promise<FodderSnapshotRow[]>;
      getLLMHistory(opts?: { limit?: number }): Promise<LLMHistoryRow[]>;
      askLLM(opts: { text: string; platform: Platform }): Promise<AskResult>;
      askMultiModel(opts: { trade_call: string; provider_ids: string[]; platform?: Platform; image_b64?: string | null }): Promise<MultiModelResult>;
      getProviderAvailability(): Promise<ProviderAvailability>;
      getProviderHealth(): Promise<Record<string, boolean>>;
      buildAskContext(opts: { trade_call: string; platform: Platform }): Promise<{ userMessage: string; context_info: { cards: string[]; signals_count: number } }>;
      callSingleProvider(opts: { provider_id: string; user_message: string; image_b64?: string | null; input_text?: string; session_id?: string }): Promise<import('./lib/types').MultiModelVerdict>;
      logAskMulti(opts: { input_text: string; verdicts: import('./lib/types').MultiModelVerdict[] }): Promise<{ ok: boolean }>;
      cancelSession(opts: { session_id: string }): Promise<{ cancelled: boolean }>;
      onProviderStatus(callback: (data: { session_id: string; provider_id: string; status: string; message: string }) => void): () => void;
      getRecommendations(opts?: { platform?: Platform; limit?: number; activeOnly?: boolean; showAll?: boolean }): Promise<unknown[]>;
      dismissRecommendation(opts: { id: number; reason?: string | null }): Promise<void>;
      requestFreshPrice(opts: { card_id?: number | null; card_key?: string | null; platform?: string }): Promise<{ status: string; error?: string }>;
      getRecommendationStats(opts?: { days?: number }): Promise<unknown>;
      triggerRecommendations(opts?: { platform?: Platform; provider_id?: string }): Promise<unknown>;
      getRecommendationBudgetStatus(): Promise<unknown>;
      getSettings(): Promise<AppSettings>;
      setSetting(key: string, value: unknown): Promise<AppSettings>;
      restartBackend(): Promise<void>;
      stopBackend(): Promise<void>;
      backendRunning(): Promise<boolean>;
    };
  }
}
