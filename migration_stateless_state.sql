-- Migration: Stateless-Aware State Machine

CREATE TABLE IF NOT EXISTS user_state (
    chat_id TEXT PRIMARY KEY,
    pomodoro_active BOOLEAN DEFAULT FALSE,
    last_user_interaction_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS calendar_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sent_reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id TEXT NOT NULL,
    reminder_type TEXT NOT NULL, -- e.g., 'task_nudge', 'deep_work_reminder', 'suspicious_silence'
    content TEXT NOT NULL,
    sent_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for duplicate check performance
CREATE INDEX IF NOT EXISTS idx_sent_reminders_chat_type_time ON sent_reminders(chat_id, reminder_type, sent_at);
