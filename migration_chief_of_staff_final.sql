-- Migration: Chief of Staff Refactor - Projects, Pillars, and Knowledge Graph

-- 1. Project Velocity Tracking
CREATE TABLE IF NOT EXISTS goals (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL, -- e.g., 'Kijiji', 'Vetted Scout', 'Portfolio'
    priority INTEGER DEFAULT 1, -- 1-10
    deadline TIMESTAMPTZ,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'paused')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES goals(id),
    content TEXT,
    activity_type TEXT, -- e.g., 'commit', 'log', 'milestone'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Habit Pillars
CREATE TABLE IF NOT EXISTS habits (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE, -- mobility_drills, skincare_routine, dopamine_integrity, deep_research
    pillar TEXT NOT NULL, -- Physical Rigor, Aesthetic Maintenance, Dopamine Discipline, Intellectual Expansion
    frequency TEXT DEFAULT 'daily',
    streak INTEGER DEFAULT 0,
    last_completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default habits
INSERT INTO habits (name, pillar) VALUES
('mobility_drills', 'Physical Rigor'),
('skincare_routine', 'Aesthetic Maintenance'),
('dopamine_integrity', 'Dopamine Discipline'),
('deep_research', 'Intellectual Expansion')
ON CONFLICT (name) DO NOTHING;

-- 3. Intellectual Growth Tracker (Knowledge Graph)
CREATE TABLE IF NOT EXISTS knowledge_graph (
    id BIGSERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
