-- Phase 9 reproducible quality assessments, evidence-linked gaps/questions,
-- and privacy-preserving daily node demand aggregates.
-- Additive migration. No raw query text is stored.

BEGIN;

CREATE TABLE IF NOT EXISTS knowledge_query_demand_daily (
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    security_partition_id VARCHAR(64) NOT NULL,
    tree_version VARCHAR(64) NOT NULL,
    node_id VARCHAR(128) NOT NULL,
    day DATE NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (
        tenant_id,
        project_id,
        security_partition_id,
        tree_version,
        node_id,
        day
    ),
    CONSTRAINT ck_knowledge_query_demand_nonnegative CHECK (request_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_query_demand_scope_day
    ON knowledge_query_demand_daily (
        tenant_id,
        project_id,
        security_partition_id,
        tree_version,
        day
    );

CREATE TABLE IF NOT EXISTS knowledge_quality_assessments (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    security_partition_id VARCHAR(64) NOT NULL,
    tree_version VARCHAR(64) NOT NULL,
    as_of TIMESTAMP WITH TIME ZONE NOT NULL,
    metric_definition_version VARCHAR(64) NOT NULL,
    scoring_formula_version VARCHAR(64) NOT NULL,
    config_version VARCHAR(64) NOT NULL,
    config_checksum VARCHAR(64) NOT NULL,
    input_checksum VARCHAR(64) NOT NULL,
    assessment_checksum VARCHAR(64) NOT NULL,
    config_json JSON NOT NULL,
    input_snapshot_json JSON NOT NULL,
    metrics_json JSON NOT NULL,
    reason_codes_json JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_knowledge_quality_assessment_identity
        UNIQUE (
            tenant_id,
            project_id,
            security_partition_id,
            tree_version,
            assessment_checksum
        )
);

CREATE INDEX IF NOT EXISTS idx_knowledge_quality_assessment_scope
    ON knowledge_quality_assessments (
        tenant_id,
        project_id,
        security_partition_id,
        tree_version,
        as_of
    );

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id VARCHAR(36) PRIMARY KEY,
    assessment_id VARCHAR(36) NOT NULL
        REFERENCES knowledge_quality_assessments(id) ON DELETE CASCADE,
    node_id VARCHAR(128) NOT NULL,
    priority DOUBLE PRECISION,
    severity DOUBLE PRECISION,
    importance DOUBLE PRECISION,
    eligible BOOLEAN NOT NULL,
    metrics_json JSON NOT NULL,
    reason_codes_json JSON NOT NULL,
    evidence_refs_json JSON NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_knowledge_gap_assessment_node UNIQUE (assessment_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_priority
    ON knowledge_gaps (assessment_id, eligible, priority);

CREATE TABLE IF NOT EXISTS knowledge_gap_questions (
    id VARCHAR(36) PRIMARY KEY,
    gap_id VARCHAR(36) NOT NULL
        REFERENCES knowledge_gaps(id) ON DELETE CASCADE,
    question_text TEXT NOT NULL,
    generator_version VARCHAR(64) NOT NULL,
    model_version VARCHAR(128) NOT NULL,
    prompt_version VARCHAR(64) NOT NULL,
    evidence_ids_json JSON NOT NULL,
    reason_codes_json JSON NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_knowledge_gap_question_checksum UNIQUE (gap_id, checksum)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_gap_questions_gap
    ON knowledge_gap_questions (gap_id, created_at);

COMMIT;
