-- Project-scoped AI Chat Mind Map snapshots: additive PostgreSQL schema.
-- Apply before dependent Mind Map persistence API deployment.
-- Manual rollback script: `SAG/apps/api/scripts/0011_project_mind_map_snapshots_down.sql`.
BEGIN;

CREATE TABLE IF NOT EXISTS project_mind_maps (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    partition_scope_hash VARCHAR(64) NOT NULL,
    topic_key VARCHAR(200) NOT NULL DEFAULT '',
    topic_display VARCHAR(200),
    scope_fingerprint VARCHAR(128) NOT NULL,
    generation_mode VARCHAR(24) NOT NULL,
    fallback_reason VARCHAR(64),
    coverage_json JSON NOT NULL,
    tree_json JSON NOT NULL,
    citations_json JSON NOT NULL DEFAULT '[]',
    used_document_version_ids JSON NOT NULL DEFAULT '[]',
    generated_by_subject VARCHAR(256) NOT NULL,
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_project_mind_maps_scope_topic UNIQUE (
        tenant_id,
        project_id,
        partition_scope_hash,
        topic_key
    ),
    CONSTRAINT ck_project_mind_maps_tenant CHECK (length(trim(tenant_id)) > 0),
    CONSTRAINT ck_project_mind_maps_project CHECK (length(trim(project_id)) > 0),
    CONSTRAINT ck_project_mind_maps_partition_hash CHECK (length(trim(partition_scope_hash)) > 0),
    CONSTRAINT ck_project_mind_maps_topic_pair CHECK (
        length(topic_key) <= 200 AND (
            (topic_key = '' AND topic_display IS NULL) OR
            (length(trim(topic_key)) > 0 AND topic_display IS NOT NULL AND length(trim(topic_display)) > 0)
        )
    ),
    CONSTRAINT ck_project_mind_maps_fingerprint CHECK (length(trim(scope_fingerprint)) > 0),
    CONSTRAINT ck_project_mind_maps_generator CHECK (length(trim(generated_by_subject)) > 0),
    CONSTRAINT ck_project_mind_maps_generation_mode CHECK (
        (generation_mode = 'llm' AND fallback_reason IS NULL) OR
        (generation_mode = 'outline_fallback' AND fallback_reason IS NOT NULL AND fallback_reason IN ('LLM_UNAVAILABLE', 'LLM_INVALID_OUTPUT'))
    )
);

CREATE INDEX IF NOT EXISTS ix_project_mind_maps_project_updated
    ON project_mind_maps (tenant_id, project_id, updated_at);

COMMIT;
