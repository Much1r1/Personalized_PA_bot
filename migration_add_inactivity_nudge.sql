-- Migration: Add last_inactivity_nudge_at to user_context
ALTER TABLE user_context ADD COLUMN IF NOT EXISTS last_inactivity_nudge_at TIMESTAMPTZ;
