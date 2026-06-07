-- Migration: Add structured timeline logging for the reminder pipeline

CREATE TABLE IF NOT EXISTS reminder_logs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    event_id TEXT,
    task_id BIGINT,
    alarm_id BIGINT,
    chat_id TEXT NOT NULL,
    reminder_type TEXT NOT NULL,
    event_start_time TIMESTAMPTZ,
    minutes_until_start INTEGER,
    matched_window TEXT,
    decision TEXT NOT NULL, -- 'sent', 'suppressed', 'skipped', 'attempted', 'failed'
    reason TEXT,
    telegram_response JSONB,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_reminder_logs_event_id ON reminder_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_reminder_logs_task_id ON reminder_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_reminder_logs_alarm_id ON reminder_logs(alarm_id);
CREATE INDEX IF NOT EXISTS idx_reminder_logs_created_at ON reminder_logs(created_at);
