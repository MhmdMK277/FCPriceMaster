ALTER TABLE signals ADD COLUMN signal_context TEXT DEFAULT 'fut_market';
CREATE INDEX IF NOT EXISTS idx_signals_context ON signals(signal_context);
