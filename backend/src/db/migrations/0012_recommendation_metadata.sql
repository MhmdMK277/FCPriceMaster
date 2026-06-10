-- Session 35: dismiss-with-reason metadata.
-- NOTE: dismissed_at already exists (added in 0006_recommendations_phase3.sql),
-- so only the flag and reason columns are added here.
ALTER TABLE recommendations ADD COLUMN dismissed INTEGER DEFAULT 0;
ALTER TABLE recommendations ADD COLUMN dismissed_reason TEXT DEFAULT NULL;

-- Backfill the flag for rows dismissed before this migration.
UPDATE recommendations SET dismissed = 1 WHERE dismissed_at IS NOT NULL;
