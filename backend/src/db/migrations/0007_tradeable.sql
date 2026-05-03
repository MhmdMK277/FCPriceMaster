-- Migration 0007: add tradeable column to cards table
-- Non-tradeable cards (SBCs, objectives, reward-only) are tracked but excluded
-- from all recommendation candidates and price snapshots.
ALTER TABLE cards ADD COLUMN tradeable INTEGER NOT NULL DEFAULT 1;
