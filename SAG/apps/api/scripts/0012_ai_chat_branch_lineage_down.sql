-- MANUAL DESTRUCTIVE ROLLBACK ONLY for `migrations/0012_ai_chat_branch_lineage.sql`.
-- Prerequisites BEFORE running:
--   1. Disable branch-lineage API routes and roll back dependent application code.
--   2. Export any branched `threads` (`parent_thread_id IS NOT NULL`) and tombstoned rows.
--   3. Obtain explicit approval from Thang (never execute automatically).

BEGIN;

DROP TRIGGER IF EXISTS trg_threads_branch_scope_guard ON threads;
DROP FUNCTION IF EXISTS fn_threads_branch_scope_guard();

DROP INDEX IF EXISTS ix_threads_owner_visible;
DROP INDEX IF EXISTS ix_threads_branch_point_message_id;
DROP INDEX IF EXISTS ix_threads_parent_thread_id;

ALTER TABLE threads DROP CONSTRAINT IF EXISTS ck_threads_no_self_parent;
ALTER TABLE threads DROP CONSTRAINT IF EXISTS ck_threads_branch_pair;

ALTER TABLE threads DROP COLUMN IF EXISTS deleted_by_owner_at;
ALTER TABLE threads DROP COLUMN IF EXISTS branch_point_message_id;
ALTER TABLE threads DROP COLUMN IF EXISTS parent_thread_id;

COMMIT;
