-- 1. BRAIN DUMPS TABLE
CREATE TABLE IF NOT EXISTS brain_dumps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    raw_content TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}',
    processed BOOLEAN DEFAULT false,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_brain_dumps_tags ON brain_dumps USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_brain_dumps_processed ON brain_dumps (processed);
