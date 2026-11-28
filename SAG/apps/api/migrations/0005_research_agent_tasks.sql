-- Phase 10 & Checkpoint D bounded research agent tasks.
-- Additive migration.
-- Persists research tasks with gap/question linkage, policy allowlist,
-- budget, lifecycle states, retry counts, and ingestion provenance.

BEGIN;

CREATE TABLE IF NOT EXISTS research_tasks (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    security_partition_id VARCHAR(64) NOT NULL,
    tree_version VARCHAR(64) NOT NULL,
    node_id VARCHAR(128) NOT NULL,
    gap_id VARCHAR(36) NOT NULL
        REFERENCES knowledge_gaps(id) ON DELETE CASCADE,
    question_id VARCHAR(36)
        REFERENCES knowledge_gap_questions(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL,
    reason_codes_json JSON NOT NULL,
    policy_json JSON NOT NULL,
    budget_json JSON NOT NULL,
    target_urls_json JSON NOT NULL,
    fetch_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    ingestion_run_ids_json JSON NOT NULL DEFAULT '[]',
    document_version_ids_json JSON NOT NULL DEFAULT '[]',
    checksum VARCHAR(64) NOT NULL,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_research_task_gap_checksum UNIQUE (gap_id, checksum)
);

CREATE INDEX IF NOT EXISTS idx_research_tasks_scope_status
    ON research_tasks (
        tenant_id,
        project_id,
        security_partition_id,
        tree_version,
        status
    );

CREATE INDEX IF NOT EXISTS idx_research_tasks_gap
    ON research_tasks (gap_id, created_at);

COMMIT;
