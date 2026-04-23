-- Migration: Fix Telegram IDs and add acknowledged_at column
-- This script hardens the schema by ensuring user/chat IDs can accommodate Telegram IDs (string/text)
-- and adds missing columns for the Nudge Engine.

DO $$
BEGIN
    -- 1. Fix pomodoro_sessions.user_id
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pomodoro_sessions' AND column_name='user_id') THEN
        ALTER TABLE pomodoro_sessions ALTER COLUMN user_id TYPE TEXT;
    END IF;

    -- 2. Fix user_tasks.user_id (in case it exists)
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='user_id') THEN
        ALTER TABLE user_tasks ALTER COLUMN user_id TYPE TEXT;
    END IF;

    -- 3. Fix user_tasks.chat_id
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='chat_id') THEN
        ALTER TABLE user_tasks ALTER COLUMN chat_id TYPE TEXT;
    END IF;

    -- 4. Add acknowledged_at to user_tasks if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='acknowledged_at') THEN
        ALTER TABLE user_tasks ADD COLUMN acknowledged_at TIMESTAMPTZ;
    END IF;
END $$;
