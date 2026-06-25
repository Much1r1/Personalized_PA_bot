-- Migration: Add notification_logs for state-driven proactive engine

CREATE TYPE notification_status AS ENUM ('dispatched', 'acknowledged', 'completed', 'stale');

CREATE TABLE IF NOT EXISTS notification_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id TEXT NOT NULL,
    notification_type TEXT NOT NULL, -- e.g., 'morning_brief', 'task_reminder', 'habit_nudge'
    entity_type TEXT,                -- e.g., 'user_tasks', 'habits'
    entity_id TEXT,                  -- ID of the associated task or habit
    content TEXT NOT NULL,
    status notification_status NOT NULL DEFAULT 'dispatched',
    nudge_count INTEGER NOT NULL DEFAULT 0,
    last_nudge_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_notification_logs_status ON notification_logs(status);
CREATE INDEX IF NOT EXISTS idx_notification_logs_chat_id ON notification_logs(chat_id);

-- Add a trigger to update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_notification_logs_updated_at
    BEFORE UPDATE ON notification_logs
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();
