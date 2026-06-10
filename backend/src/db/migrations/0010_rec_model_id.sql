-- 0010: add model_id column to recommendations table
ALTER TABLE recommendations ADD COLUMN model_id TEXT DEFAULT 'claude-haiku-4-5-20251001';
