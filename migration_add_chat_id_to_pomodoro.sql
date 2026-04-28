-- Migration: Add chat_id to pomodoro_sessions
ALTER TABLE pomodoro_sessions ADD COLUMN IF NOT EXISTS chat_id TEXT;
