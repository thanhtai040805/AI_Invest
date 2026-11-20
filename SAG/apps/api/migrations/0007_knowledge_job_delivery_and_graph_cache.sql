BEGIN;
ALTER TABLE knowledge_jobs ADD COLUMN IF NOT EXISTS tree_requested_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE knowledge_graph_builds ADD COLUMN IF NOT EXISTS input_fingerprint VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_graph_input
  ON knowledge_graph_builds (tenant_id, project_id, input_fingerprint);
COMMIT;
