-- Phase 6B: Initial DB schema for QA Agent
-- Executed on first docker-compose up

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Sessions
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL,
    environment VARCHAR(32) NOT NULL DEFAULT 'sit',
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_by VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    config JSONB DEFAULT '{}'::jsonb
);

-- Test Runs
CREATE TABLE IF NOT EXISTS test_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    run_number SERIAL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    total_tests INT DEFAULT 0,
    passed INT DEFAULT 0,
    failed INT DEFAULT 0,
    skipped INT DEFAULT 0,
    pass_rate NUMERIC(5,2) DEFAULT 0,
    duration_ms BIGINT DEFAULT 0,
    gate_verdict VARCHAR(8),
    gate_score NUMERIC(5,2),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Test Results
CREATE TABLE IF NOT EXISTS test_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID REFERENCES test_runs(id) ON DELETE CASCADE,
    test_name VARCHAR(512) NOT NULL,
    module VARCHAR(128),
    category VARCHAR(64),
    status VARCHAR(16) NOT NULL,
    duration_ms INT DEFAULT 0,
    error_message TEXT,
    stack_trace TEXT,
    screenshot_url TEXT,
    is_flaky BOOLEAN DEFAULT FALSE,
    flaky_count INT DEFAULT 0,
    is_regression BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Bugs
CREATE TABLE IF NOT EXISTS bugs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tracker_id VARCHAR(64),
    tracker_type VARCHAR(16) DEFAULT 'ado',
    run_id UUID REFERENCES test_runs(id),
    title VARCHAR(512) NOT NULL,
    description TEXT,
    severity VARCHAR(16) NOT NULL DEFAULT 'medium',
    status VARCHAR(32) NOT NULL DEFAULT 'new',
    assigned_to VARCHAR(128),
    module VARCHAR(128),
    url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Feedback
CREATE TABLE IF NOT EXISTS feedback (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    feedback_type VARCHAR(16) NOT NULL,
    source VARCHAR(16) NOT NULL DEFAULT 'ui',
    target_type VARCHAR(32) NOT NULL,
    target_id VARCHAR(128) NOT NULL,
    reviewer VARCHAR(128) NOT NULL,
    comment TEXT,
    old_value JSONB,
    new_value JSONB,
    confidence_delta NUMERIC(4,3) DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Discovery cache
CREATE TABLE IF NOT EXISTS discovery_cache (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url TEXT NOT NULL,
    page_type VARCHAR(32),
    content_hash VARCHAR(64),
    elements JSONB,
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_runs_session ON test_runs(session_id);
CREATE INDEX IF NOT EXISTS idx_results_run ON test_results(run_id);
CREATE INDEX IF NOT EXISTS idx_results_status ON test_results(status);
CREATE INDEX IF NOT EXISTS idx_bugs_run ON bugs(run_id);
CREATE INDEX IF NOT EXISTS idx_bugs_severity ON bugs(severity);
CREATE INDEX IF NOT EXISTS idx_feedback_target ON feedback(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_discovery_url ON discovery_cache USING gin(url gin_trgm_ops);
