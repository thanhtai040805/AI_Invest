-- Phase 5 knowledge truth, queue and inactive candidates. Apply after 0001/0002.

-- Additive: retain these tables during app rollback; no active slot changes.

BEGIN;

CREATE TABLE IF NOT EXISTS knowledge_units (
	id VARCHAR(36) NOT NULL,
	document_version_id VARCHAR(36) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	project_id VARCHAR(64) NOT NULL,
	source_id VARCHAR(128) NOT NULL,
	security_partition_id VARCHAR(64) NOT NULL,
	ordinal INTEGER NOT NULL,
	text TEXT NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	input_checksum VARCHAR(64) NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	is_current BOOLEAN NOT NULL,
	provenance_json JSON NOT NULL,
	features_json JSON NOT NULL,
	valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
	valid_to TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(document_version_id) REFERENCES document_versions (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_units_scope ON knowledge_units (tenant_id, project_id, security_partition_id);

CREATE INDEX IF NOT EXISTS ix_knowledge_units_document_version_id ON knowledge_units (document_version_id);

CREATE TABLE IF NOT EXISTS knowledge_evidence (
	id VARCHAR(36) NOT NULL,
	unit_id VARCHAR(36) NOT NULL,
	tier VARCHAR(8) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	extractor_version VARCHAR(128) NOT NULL,
	confidence FLOAT NOT NULL,
	payload_json JSON NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(unit_id) REFERENCES knowledge_units (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_knowledge_evidence_unit_id ON knowledge_evidence (unit_id);

CREATE TABLE IF NOT EXISTS knowledge_jobs (
	id VARCHAR(36) NOT NULL,
	document_version_id VARCHAR(36) NOT NULL,
	unit_id VARCHAR(36),
	result_json JSON,
	extractor_version VARCHAR(128),
	tenant_id VARCHAR(64) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	input_checksum VARCHAR(64) NOT NULL,
	status VARCHAR(16) NOT NULL,
	priority INTEGER NOT NULL,
	attempts INTEGER NOT NULL,
	reserved_tokens INTEGER NOT NULL,
	lease_token VARCHAR(36),
	lease_until TIMESTAMP WITH TIME ZONE,
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	error_code VARCHAR(64),
	PRIMARY KEY (id),
	FOREIGN KEY(document_version_id) REFERENCES document_versions (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_dispatch ON knowledge_jobs (status, available_at, priority);

CREATE INDEX IF NOT EXISTS ix_knowledge_jobs_document_version_id ON knowledge_jobs (document_version_id);

CREATE TABLE IF NOT EXISTS knowledge_queue_control (
	id VARCHAR(128) NOT NULL,
	tokens_reserved INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS knowledge_graph_builds (
	id VARCHAR(64) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	project_id VARCHAR(64) NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	manifest_json JSON NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_knowledge_graph_builds_project_id ON knowledge_graph_builds (project_id);

CREATE INDEX IF NOT EXISTS ix_knowledge_graph_builds_tenant_id ON knowledge_graph_builds (tenant_id);

CREATE TABLE IF NOT EXISTS knowledge_unit_edges (
	build_id VARCHAR(64) NOT NULL,
	source_unit_id VARCHAR(36) NOT NULL,
	target_unit_id VARCHAR(36) NOT NULL,
	security_partition_id VARCHAR(64) NOT NULL,
	weight FLOAT NOT NULL,
	signals_json JSON NOT NULL,
	PRIMARY KEY (build_id, source_unit_id, target_unit_id),
	FOREIGN KEY(build_id) REFERENCES knowledge_graph_builds (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_tree_nodes (
	tree_version VARCHAR(64) NOT NULL,
	node_id VARCHAR(128) NOT NULL,
	parent_id VARCHAR(128),
	security_partition_id VARCHAR(64) NOT NULL,
	payload_json JSON NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	PRIMARY KEY (tree_version, node_id),
	FOREIGN KEY(tree_version) REFERENCES tree_manifests (tree_version) ON DELETE CASCADE
);

ALTER TABLE knowledge_jobs ADD COLUMN IF NOT EXISTS result_json JSON;

ALTER TABLE knowledge_jobs ADD COLUMN IF NOT EXISTS extractor_version VARCHAR(128);

ALTER TABLE knowledge_jobs DROP CONSTRAINT IF EXISTS knowledge_jobs_unit_id_fkey;

COMMIT;
