-- Migration: Add mute functionality to user_state
ALTER TABLE user_state ADD COLUMN IF NOT EXISTS is_muted BOOLEAN DEFAULT FALSE;
ALTER TABLE user_state ADD COLUMN IF NOT EXISTS muted_until TIMESTAMPTZ;
