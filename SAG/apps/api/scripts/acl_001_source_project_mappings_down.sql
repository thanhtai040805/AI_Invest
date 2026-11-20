-- Rollback removes the mapping authority and disables all mapped Sources.
-- It intentionally does not update Source data or restore global search.
BEGIN;
DROP INDEX IF EXISTS uq_source_project_mappings_current_source;
DROP INDEX IF EXISTS ix_source_project_mappings_scope;
DROP INDEX IF EXISTS ix_source_project_mappings_source_id;
DROP TABLE IF EXISTS source_project_mappings;
COMMIT;
