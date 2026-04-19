-- Migration Script: Schema Hardening for user_alarms and user_tasks

-- Audit and update user_alarms
DO $$
BEGIN
    -- Add status if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_alarms' AND column_name='status') THEN
        ALTER TABLE user_alarms ADD COLUMN status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'triggered', 'acknowledged'));
    END IF;

    -- Add triggered_at if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_alarms' AND column_name='triggered_at') THEN
        ALTER TABLE user_alarms ADD COLUMN triggered_at TIMESTAMPTZ;
    END IF;

    -- Add acknowledged_at if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_alarms' AND column_name='acknowledged_at') THEN
        ALTER TABLE user_alarms ADD COLUMN acknowledged_at TIMESTAMPTZ;
    END IF;

    -- Add created_at if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_alarms' AND column_name='created_at') THEN
        ALTER TABLE user_alarms ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();
    END IF;

    -- Add payload if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_alarms' AND column_name='payload') THEN
        ALTER TABLE user_alarms ADD COLUMN payload JSONB DEFAULT '{}';
    END IF;

    -- Add metadata if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_alarms' AND column_name='metadata') THEN
        ALTER TABLE user_alarms ADD COLUMN metadata JSONB DEFAULT '{}';
    END IF;
END $$;

-- Audit and update user_tasks
DO $$
BEGIN
    -- Add status if missing (already in schema.sql but hardening)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='status') THEN
        ALTER TABLE user_tasks ADD COLUMN status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed'));
    END IF;

    -- Add triggered_at if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='triggered_at') THEN
        ALTER TABLE user_tasks ADD COLUMN triggered_at TIMESTAMPTZ;
    END IF;

    -- Add acknowledged_at if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='acknowledged_at') THEN
        ALTER TABLE user_tasks ADD COLUMN acknowledged_at TIMESTAMPTZ;
    END IF;

    -- Add created_at if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='created_at') THEN
        ALTER TABLE user_tasks ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();
    END IF;

    -- Add payload if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='payload') THEN
        ALTER TABLE user_tasks ADD COLUMN payload JSONB DEFAULT '{}';
    END IF;

    -- Add metadata if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='user_tasks' AND column_name='metadata') THEN
        ALTER TABLE user_tasks ADD COLUMN metadata JSONB DEFAULT '{}';
    END IF;
END $$;
