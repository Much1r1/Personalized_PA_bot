-- schema.sql update for proactive features

CREATE TABLE IF NOT EXISTS user_schedules (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT UNIQUE,
    summary TEXT NOT NULL,
    description TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_tasks (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
    due_date TIMESTAMPTZ,
    effort_score INTEGER,
    impact_score INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    triggered_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_alarms (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    alarm_time TIMESTAMPTZ NOT NULL,
    message TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'triggered', 'acknowledged')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    triggered_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ
);
