-- Migration: Proactive Assistant Features

-- 1. Enhance user_schedules for location and multi-user support
ALTER TABLE user_schedules ADD COLUMN IF NOT EXISTS location TEXT;
ALTER TABLE user_schedules ADD COLUMN IF NOT EXISTS chat_id TEXT;

-- 2. Enhance sent_reminders for event-specific tracking and duplicate prevention
ALTER TABLE sent_reminders ADD COLUMN IF NOT EXISTS event_id TEXT;
ALTER TABLE sent_reminders ADD COLUMN IF NOT EXISTS event_start_time TIMESTAMPTZ;
ALTER TABLE sent_reminders ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

-- 3. Ensure indexes for performance
-- 3. Ensure system_config table exists (referenced in sync_service.py)
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Ensure indexes for performance
CREATE INDEX IF NOT EXISTS idx_user_schedules_start_time ON user_schedules(start_time);
CREATE INDEX IF NOT EXISTS idx_sent_reminders_event_id ON sent_reminders(event_id);
