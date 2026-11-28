-- ============================================================================
-- Phase 0: Knowledge Routing RAG Schema & Upgrades
-- Production DDL for PostgreSQL / SQLite
-- ============================================================================

-- 1. Table: document_versions
CREATE TABLE IF NOT EXISTS document_versions (
    id VARCHAR(64) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    supersedes_id VARCHAR(64) REFERENCES document_versions(id) ON DELETE SET NULL,
    source_published_at TIMESTAMP WITH TIME ZONE,
    valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
    valid_to TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'RECEIVED',
    search_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    search_ready_at TIMESTAMP WITH TIME ZONE,
    knowledge_status VARCHAR(32) NOT NULL DEFAULT 'NOT_STARTED',
    knowledge_ready_at TIMESTAMP WITH TIME ZONE,
    metadata_json JSON NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_doc_ver_document_version ON document_versions (document_id, version_no);
CREATE INDEX IF NOT EXISTS ix_doc_ver_file_hash ON document_versions (file_hash);
CREATE INDEX IF NOT EXISTS ix_doc_ver_temporal ON document_versions (document_id, valid_from, valid_to);

-- 2. Table: source_snapshots
CREATE TABLE IF NOT EXISTS source_snapshots (
    id VARCHAR(36) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    document_version_id VARCHAR(64) NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    storage_uri VARCHAR(1024) NOT NULL,
    original_filename VARCHAR(512) NOT NULL,
    mime_type VARCHAR(128) NOT NULL DEFAULT 'application/octet-stream',
    byte_size INTEGER NOT NULL DEFAULT 0,
    checksum_sha256 VARCHAR(64) NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_source_snapshots_version ON source_snapshots (document_version_id);

-- 3. Table: ingestion_runs
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id VARCHAR(36) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    document_version_id VARCHAR(64) NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    idempotency_key VARCHAR(128) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    current_stage VARCHAR(32) NOT NULL DEFAULT 'RECEIVE',
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_DISPATCH',
    error_layer VARCHAR(32),
    error_stage VARCHAR(32),
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_runs_tenant_project_idempotency 
    ON ingestion_runs (tenant_id, project_id, idempotency_key);
CREATE INDEX IF NOT EXISTS ix_ingestion_runs_version_status 
    ON ingestion_runs (document_version_id, status);

-- 4. Table: stage_runs
CREATE TABLE IF NOT EXISTS stage_runs (
    id VARCHAR(36) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_id VARCHAR(36) NOT NULL REFERENCES ingestion_runs(id) ON DELETE CASCADE,
    stage VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    metrics_json JSON NOT NULL DEFAULT '{}',
    error_json JSON
);

CREATE INDEX IF NOT EXISTS ix_stage_runs_run_stage ON stage_runs (run_id, stage);

-- 5. Table: knowledge_graph_edges
CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
    id VARCHAR(36) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    document_version_id VARCHAR(64) NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    source_entity_id VARCHAR(128) NOT NULL,
    target_entity_id VARCHAR(128) NOT NULL,
    relation_type VARCHAR(64) NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    metadata_json JSON NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_kg_edges_doc_ver ON knowledge_graph_edges (document_version_id);
CREATE INDEX IF NOT EXISTS ix_kg_edges_entities ON knowledge_graph_edges (source_entity_id, target_entity_id);

-- 6. Upgrades to existing `documents` table
-- ALTER TABLE documents ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_continuum_default';
-- ALTER TABLE documents ADD COLUMN IF NOT EXISTS project_id VARCHAR(64);
-- ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_id VARCHAR(64);
-- ALTER TABLE documents ADD COLUMN IF NOT EXISTS logical_source_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS ix_documents_tenant_project_logical 
    ON documents (tenant_id, project_id, logical_source_id);
