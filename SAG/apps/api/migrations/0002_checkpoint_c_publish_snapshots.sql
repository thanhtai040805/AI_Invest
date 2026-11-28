-- Checkpoint C control plane and durable in-flight query pins.
-- Additive and safe to keep if application code is rolled back.

CREATE TABLE IF NOT EXISTS project_search_state (
    project_id VARCHAR(64) PRIMARY KEY,
    slot_a_tree_version VARCHAR(64),
    slot_b_tree_version VARCHAR(64),
    active_routing_slot VARCHAR(16) NOT NULL DEFAULT 'SLOT_A',
    active_tree_version VARCHAR(64),
    previous_tree_version VARCHAR(64),
    active_search_epoch BIGINT NOT NULL DEFAULT 1,
    last_switched_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tree_manifests (
    tree_version VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL,
    config_version VARCHAR(32) NOT NULL,
    node_count INTEGER NOT NULL,
    leaf_count INTEGER NOT NULL,
    max_leaf_size INTEGER NOT NULL,
    giant_ratio DOUBLE PRECISION NOT NULL,
    routing_recall_at_k DOUBLE PRECISION NOT NULL,
    escape_win_rate DOUBLE PRECISION NOT NULL,
    acl_blackhole_rate DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'INACTIVE',
    checksum VARCHAR(64) NOT NULL,
    manifest_json JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE tree_manifests
    ADD COLUMN IF NOT EXISTS manifest_json JSON NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_tree_manifests_project
    ON tree_manifests (project_id, status);

CREATE TABLE IF NOT EXISTS tree_snapshot_leases (
    request_snapshot_id VARCHAR(36) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    routing_slot VARCHAR(16) NOT NULL,
    tree_version VARCHAR(64) NOT NULL,
    search_epoch BIGINT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_snapshot_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_tree_snapshot_leases_slot_expiry
    ON tree_snapshot_leases (project_id, routing_slot, expires_at);

CREATE TABLE IF NOT EXISTS tree_routing_profiles (
    project_id VARCHAR(64) NOT NULL,
    tree_version VARCHAR(64) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    document_version_id VARCHAR(128) NOT NULL,
    partition_id VARCHAR(128) NOT NULL,
    node_id VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    parent_id VARCHAR(128),
    is_leaf BOOLEAN NOT NULL,
    accessible_unit_count INTEGER NOT NULL,
    sparse_json JSON NOT NULL,
    entities_json JSON NOT NULL,
    profile_checksum VARCHAR(64) NOT NULL,
    PRIMARY KEY (project_id, tree_version, source_id, document_version_id, partition_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_tree_routing_profiles_version_scope
    ON tree_routing_profiles (project_id, tree_version, source_id, document_version_id, partition_id);

-- Rollback: stop Checkpoint C publishers/readers first, then roll back the app.
-- Keep project_search_state, tree_manifests, and tree_routing_profiles so
-- active/previous pointer history and verified query profiles remain available.
-- The lease table and manifest_json column are additive and are also safe to
-- retain during application rollback.
