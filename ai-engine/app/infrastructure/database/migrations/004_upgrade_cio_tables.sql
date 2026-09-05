-- Migration 004: Upgrade Strategy CIO Tables (IOS v5.1 Institutional Sovereign Architecture)

-- 1. Upgrade cio_resolutions
ALTER TABLE cio_resolutions ALTER COLUMN final_resolution TYPE VARCHAR(64);
ALTER TABLE cio_resolutions ALTER COLUMN thesis_id DROP NOT NULL;

ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS decision_type VARCHAR(64) DEFAULT 'CONFLICT_RESOLUTION';
ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS ticker VARCHAR(16);
ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS verdict_payload JSONB DEFAULT '{}'::jsonb;
ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS previous_hash VARCHAR(64) DEFAULT '0000000000000000000000000000000000000000000000000000000000000000';
ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS decision_hash VARCHAR(64);
ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS governance_cosign BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_cio_resolutions_hash ON cio_resolutions (decision_hash);
CREATE INDEX IF NOT EXISTS idx_cio_resolutions_created_at ON cio_resolutions (created_at DESC);

-- 2. Create cio_strategic_directives table
CREATE TABLE IF NOT EXISTS cio_strategic_directives (
    directive_id VARCHAR(64) PRIMARY KEY,
    policy_version VARCHAR(32) NOT NULL DEFAULT 'v5.1_IOS',
    effective_from DATE NOT NULL,
    effective_until DATE,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    macro_regime VARCHAR(32) NOT NULL,
    risk_appetite VARCHAR(32) NOT NULL,
    strategic_cash_target_pct NUMERIC(6,2) NOT NULL,
    sector_tilt JSONB NOT NULL DEFAULT '{}'::jsonb,
    flash_invalidation_thresholds JSONB DEFAULT '{}'::jsonb,
    rationale TEXT NOT NULL,
    decision_hash VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cio_directives_status ON cio_strategic_directives (status);
CREATE INDEX IF NOT EXISTS idx_cio_directives_effective ON cio_strategic_directives (effective_from, effective_until);
