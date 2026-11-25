-- ============================================================================
-- Migration 0014: SAG-Native User Memory & Linked Sessions (Tri-Temporal Mem0-g)
-- Idempotent PostgreSQL 16 DDL matching sag_api/db/models/user_memory.py.
-- Strictly isolated by (tenant_id, user_id); never mutates shared kg_nodes/kg_edges.
-- ============================================================================

BEGIN;

-- 1. Per-User Session Episodes (Backbone of Linked Sessions + Tombstone Guard)
CREATE TABLE IF NOT EXISTS user_memory_episodes (
    episode_id                 VARCHAR(64) PRIMARY KEY,
    tenant_id                  VARCHAR(64) NOT NULL,
    user_id                    VARCHAR(256) NOT NULL,
    external_session_id        VARCHAR(128) NOT NULL,
    parent_session_id          VARCHAR(128),
    session_title              TEXT,
    chat_mode                  VARCHAR(16) NOT NULL DEFAULT 'GENERAL',
    turn_range_start           INT NOT NULL DEFAULT 1,
    turn_range_end             INT NOT NULL DEFAULT 1,
    source_message_ids_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    tickers_json               JSONB NOT NULL DEFAULT '[]'::jsonb,
    sectors_json               JSONB NOT NULL DEFAULT '[]'::jsonb,
    macro_factors_json         JSONB NOT NULL DEFAULT '[]'::jsonb,
    topic_tags_json            JSONB NOT NULL DEFAULT '[]'::jsonb,
    cited_doc_version_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    episode_summary            TEXT NOT NULL DEFAULT '',
    staleness_status           VARCHAR(24) NOT NULL DEFAULT 'FRESH',
    staleness_reason           TEXT,
    source_as_of_date          DATE NOT NULL,
    is_backtest_sandbox        BOOLEAN NOT NULL DEFAULT FALSE,
    status                     VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_memory_episodes_scope_session UNIQUE (tenant_id, user_id, external_session_id)
);

CREATE INDEX IF NOT EXISTS idx_user_memory_episodes_scope_pit
    ON user_memory_episodes (tenant_id, user_id, status, is_backtest_sandbox, source_as_of_date);
CREATE INDEX IF NOT EXISTS idx_user_memory_episodes_parent
    ON user_memory_episodes (tenant_id, user_id, parent_session_id);

-- 2. Tri-Temporal Mem0-g User Memory Items + 3-State Versioned Qdrant Outbox
CREATE TABLE IF NOT EXISTS user_memory_items (
    memory_id                  VARCHAR(64) PRIMARY KEY,
    tenant_id                  VARCHAR(64) NOT NULL,
    user_id                    VARCHAR(256) NOT NULL,
    scope                      VARCHAR(24) NOT NULL DEFAULT 'USER_GLOBAL',
    memory_type                VARCHAR(32) NOT NULL,
    pit_sensitivity            VARCHAR(24) NOT NULL DEFAULT 'MARKET_BOUND',
    canonical_slot_key         VARCHAR(160),
    content_text               TEXT NOT NULL,
    structured_payload_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    tickers_json               JSONB NOT NULL DEFAULT '[]'::jsonb,
    bound_doc_version_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence                 DOUBLE PRECISION NOT NULL DEFAULT 0.85,
    source_authority           VARCHAR(32) NOT NULL DEFAULT 'USER_DECLARED',
    origin_episode_id          VARCHAR(64) REFERENCES user_memory_episodes(episode_id) ON DELETE SET NULL,
    origin_session_id          VARCHAR(128),
    origin_message_id          VARCHAR(128),
    source_as_of_date          DATE NOT NULL,
    is_backtest_sandbox        BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to                   TIMESTAMPTZ NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    tx_from                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    tx_to                      TIMESTAMPTZ NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    supersedes_memory_id       VARCHAR(64) REFERENCES user_memory_items(memory_id) ON DELETE SET NULL,
    merged_from_ids_json       JSONB NOT NULL DEFAULT '[]'::jsonb,
    invalidation_reason        TEXT,
    staleness_status           VARCHAR(24) NOT NULL DEFAULT 'FRESH',
    staleness_reason           TEXT,
    reinforcement_count        INT NOT NULL DEFAULT 1,
    last_accessed_at           TIMESTAMPTZ,
    pinned_by_user             BOOLEAN NOT NULL DEFAULT FALSE,
    content_hash               CHAR(64) NOT NULL,
    embedding_model_signature  VARCHAR(128),
    qdrant_point_id            VARCHAR(64) NOT NULL,
    qdrant_sync_status         VARCHAR(24) NOT NULL DEFAULT 'PENDING_UPSERT',
    sync_version               BIGINT NOT NULL DEFAULT 1,
    sync_attempts              INT NOT NULL DEFAULT 0,
    last_sync_error            TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_memory_items_active_pit
    ON user_memory_items (tenant_id, user_id, is_backtest_sandbox, pit_sensitivity, valid_to, tx_to, source_as_of_date);
CREATE INDEX IF NOT EXISTS idx_user_memory_items_slot
    ON user_memory_items (tenant_id, user_id, canonical_slot_key, valid_to, tx_to);
CREATE INDEX IF NOT EXISTS idx_user_memory_items_hash
    ON user_memory_items (tenant_id, user_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_user_memory_items_session
    ON user_memory_items (tenant_id, user_id, origin_session_id);
CREATE INDEX IF NOT EXISTS idx_user_memory_items_outbox
    ON user_memory_items (qdrant_sync_status, updated_at);

-- 3. Personal Graph Entities (canonical_key aligns 1-1 with kg_nodes.node_id on read)
CREATE TABLE IF NOT EXISTS user_memory_entities (
    entity_id      VARCHAR(64) PRIMARY KEY,
    tenant_id      VARCHAR(64) NOT NULL,
    user_id        VARCHAR(256) NOT NULL,
    entity_type    VARCHAR(24) NOT NULL,
    canonical_key  VARCHAR(160) NOT NULL,
    display_name   TEXT NOT NULL,
    aliases_json   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_memory_entities_scope_key UNIQUE (tenant_id, user_id, canonical_key)
);

CREATE INDEX IF NOT EXISTS idx_user_memory_entities_scope_type
    ON user_memory_entities (tenant_id, user_id, entity_type);

-- 4. Tri-Temporal Personal Graph Edges (Closed 16-Relation Ontology)
CREATE TABLE IF NOT EXISTS user_memory_edges (
    edge_id               VARCHAR(64) PRIMARY KEY,
    tenant_id             VARCHAR(64) NOT NULL,
    user_id               VARCHAR(256) NOT NULL,
    src_entity_id         VARCHAR(64) NOT NULL REFERENCES user_memory_entities(entity_id) ON DELETE CASCADE,
    dst_entity_id         VARCHAR(64) NOT NULL REFERENCES user_memory_entities(entity_id) ON DELETE CASCADE,
    relation_type         VARCHAR(40) NOT NULL,
    conflict_group        VARCHAR(24),
    weight                DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    fact_text             TEXT NOT NULL DEFAULT '',
    memory_id             VARCHAR(64) REFERENCES user_memory_items(memory_id) ON DELETE SET NULL,
    origin_session_id     VARCHAR(128),
    source_as_of_date     DATE NOT NULL,
    is_backtest_sandbox   BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from            TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to              TIMESTAMPTZ NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    tx_from               TIMESTAMPTZ NOT NULL DEFAULT now(),
    tx_to                 TIMESTAMPTZ NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    superseded_by_edge_id VARCHAR(64) REFERENCES user_memory_edges(edge_id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_memory_edges_src_active
    ON user_memory_edges (tenant_id, user_id, src_entity_id, relation_type, valid_to, tx_to);
CREATE INDEX IF NOT EXISTS idx_user_memory_edges_dst_active
    ON user_memory_edges (tenant_id, user_id, dst_entity_id, relation_type, valid_to, tx_to);
CREATE INDEX IF NOT EXISTS idx_user_memory_edges_conflict
    ON user_memory_edges (tenant_id, user_id, src_entity_id, dst_entity_id, conflict_group, valid_to, tx_to);
CREATE INDEX IF NOT EXISTS idx_user_memory_edges_session
    ON user_memory_edges (tenant_id, user_id, origin_session_id);

-- 5. Dedicated UserMemoryWorker Durable Queue (Decoupled from InProcessAsyncQueue / MinerU)
CREATE TABLE IF NOT EXISTS user_memory_jobs (
    job_id          VARCHAR(64) PRIMARY KEY,
    idempotency_key CHAR(64) NOT NULL UNIQUE,
    job_kind        VARCHAR(32) NOT NULL,
    tenant_id       VARCHAR(64) NOT NULL,
    user_id         VARCHAR(256) NOT NULL,
    session_id      VARCHAR(128),
    payload_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
    attempts        INT NOT NULL DEFAULT 0,
    available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_token     VARCHAR(64),
    lease_until     TIMESTAMPTZ,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_memory_jobs_poll
    ON user_memory_jobs (status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_user_memory_jobs_scope
    ON user_memory_jobs (tenant_id, user_id, session_id);

-- 6. Immutable User Memory Audit Log
CREATE TABLE IF NOT EXISTS user_memory_audit_log (
    audit_id         VARCHAR(64) PRIMARY KEY,
    tenant_id        VARCHAR(64) NOT NULL,
    user_id          VARCHAR(256) NOT NULL,
    memory_id        VARCHAR(64),
    action           VARCHAR(32) NOT NULL,
    actor            VARCHAR(32) NOT NULL DEFAULT 'WORKER',
    llm_model        VARCHAR(128),
    prompt_version   VARCHAR(32),
    before_json      JSONB,
    after_json       JSONB,
    reason           TEXT,
    token_usage_json JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_memory_audit_scope_created
    ON user_memory_audit_log (tenant_id, user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_user_memory_audit_memory
    ON user_memory_audit_log (memory_id);

COMMIT;
