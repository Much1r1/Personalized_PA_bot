-- Migration: Create user_context for executive state management

CREATE TABLE IF NOT EXISTS user_context (
    chat_id TEXT PRIMARY KEY,
    last_interaction_at TIMESTAMPTZ DEFAULT NOW(),
    current_block_id TEXT, -- ID of the calendar event or pomodoro session
    current_block_type TEXT, -- 'deep_work', 'ai_engineering', 'pomodoro', or NULL
    last_briefing_at TIMESTAMPTZ,
    last_suspicious_silence_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
