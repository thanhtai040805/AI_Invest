-- MANUAL DESTRUCTIVE ROLLBACK ONLY for `migrations/0011_project_mind_map_snapshots.sql`.
-- Prerequisites BEFORE running:
--   1. Roll back dependent Mind Map snapshot persistence API code.
--   2. Obtain explicit approval from Thang (never execute automatically).

BEGIN;

DROP INDEX IF EXISTS ix_project_mind_maps_project_updated;
DROP TABLE IF EXISTS project_mind_maps;

COMMIT;
