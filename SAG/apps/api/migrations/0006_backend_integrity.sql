-- Backend integrity fixes: wider canonical document IDs, owner scopes, research state,
-- and aliases for content-duplicate upload idempotency keys.
BEGIN;

ALTER TABLE agents ADD COLUMN IF NOT EXISTS owner_key VARCHAR(64);
ALTER TABLE threads ADD COLUMN IF NOT EXISTS owner_key VARCHAR(64);
ALTER TABLE research_tasks ADD COLUMN IF NOT EXISTS execution_json JSON NOT NULL DEFAULT '{}';
ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);
ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS research_task_id VARCHAR(36) REFERENCES research_tasks(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_content_hash ON ingestion_runs (tenant_id, project_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_research_task ON ingestion_runs (research_task_id);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_id VARCHAR(256);
ALTER TABLE documents ALTER COLUMN owner_id TYPE VARCHAR(256);

CREATE INDEX IF NOT EXISTS ix_agents_owner_key ON agents (owner_key);
CREATE INDEX IF NOT EXISTS ix_threads_owner_key ON threads (owner_key);

CREATE TABLE IF NOT EXISTS upload_idempotency_keys (
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    run_id VARCHAR(36) NOT NULL REFERENCES ingestion_runs(id) ON DELETE CASCADE,
    PRIMARY KEY (tenant_id, project_id, idempotency_key)
);

DO $$
DECLARE fk RECORD;
BEGIN
  CREATE TEMP TABLE sag_document_fks ON COMMIT DROP AS
    SELECT conrelid::regclass AS rel, conname, pg_get_constraintdef(oid) AS definition
    FROM pg_constraint
    WHERE contype = 'f' AND confrelid = 'documents'::regclass;
  CREATE TEMP TABLE sag_document_fk_columns ON COMMIT DROP AS
    SELECT DISTINCT c.conrelid::regclass AS rel, local_col.attname AS column_name
    FROM pg_constraint c
    CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS keys(local_attnum, target_attnum)
    JOIN pg_attribute local_col ON local_col.attrelid = c.conrelid AND local_col.attnum = keys.local_attnum
    JOIN pg_attribute target_col ON target_col.attrelid = c.confrelid AND target_col.attnum = keys.target_attnum
    WHERE c.contype = 'f' AND c.confrelid = 'documents'::regclass AND target_col.attname = 'id';
  FOR fk IN SELECT * FROM sag_document_fks LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', fk.rel, fk.conname);
  END LOOP;
  ALTER TABLE documents ALTER COLUMN id TYPE VARCHAR(36);
  FOR fk IN SELECT * FROM sag_document_fk_columns LOOP
    EXECUTE format('ALTER TABLE %s ALTER COLUMN %I TYPE VARCHAR(36)', fk.rel, fk.column_name);
  END LOOP;
  FOR fk IN SELECT * FROM sag_document_fks LOOP
    EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s', fk.rel, fk.conname, fk.definition);
  END LOOP;
END $$;

COMMIT;
