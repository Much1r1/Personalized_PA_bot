-- Migration: Fix RLS for pomodoro_sessions
-- This script enables RLS and adds secure policies.

-- 1. Enable Row Level Security
ALTER TABLE pomodoro_sessions ENABLE ROW LEVEL SECURITY;

-- 2. Drop existing policies
DROP POLICY IF EXISTS "Allow authenticated insert" ON pomodoro_sessions;
DROP POLICY IF EXISTS "Allow authenticated select" ON pomodoro_sessions;
DROP POLICY IF EXISTS "Allow authenticated update" ON pomodoro_sessions;
DROP POLICY IF EXISTS "Allow authenticated delete" ON pomodoro_sessions;
DROP POLICY IF EXISTS "Allow users to manage their own sessions" ON pomodoro_sessions;

-- 3. Create a secure policy for authenticated users
-- This policy ensures users can only see and manage their own data.
-- It requires that the user_id column matches the authenticated user's ID.
-- We cast user_id to TEXT because auth.uid() returns a UUID.
CREATE POLICY "Allow users to manage their own sessions" ON pomodoro_sessions
FOR ALL TO authenticated
USING (auth.uid()::text = user_id::text)
WITH CHECK (auth.uid()::text = user_id::text);

-- 4. Special considerations for M-bot
-- If the bot uses the 'service_role' key, it bypasses RLS automatically.
-- If the bot needs to act on behalf of users without their JWT, it MUST use the service_role key.
-- The following ensures the service_role has full access (usually default).
GRANT ALL ON TABLE pomodoro_sessions TO service_role;
GRANT ALL ON TABLE pomodoro_sessions TO postgres;
