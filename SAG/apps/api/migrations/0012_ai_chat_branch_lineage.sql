-- AI chat conversation branch lineage ("Branch in new chat" — Option 3):
-- Additive PostgreSQL schema on `threads` with Direct Origin Pointer invariants,
-- lineage immutability, and Tombstone Reference-Counted GC (`deleted_by_owner_at`).
-- Manual rollback script: `SAG/apps/api/scripts/0012_ai_chat_branch_lineage_down.sql`.
BEGIN;

ALTER TABLE threads ADD COLUMN IF NOT EXISTS parent_thread_id VARCHAR(32)
    REFERENCES threads(id) ON DELETE RESTRICT;
ALTER TABLE threads ADD COLUMN IF NOT EXISTS branch_point_message_id VARCHAR(32)
    REFERENCES messages(id) ON DELETE RESTRICT;
ALTER TABLE threads ADD COLUMN IF NOT EXISTS deleted_by_owner_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE threads DROP CONSTRAINT IF EXISTS ck_threads_branch_pair;
ALTER TABLE threads ADD CONSTRAINT ck_threads_branch_pair CHECK (
    (parent_thread_id IS NULL AND branch_point_message_id IS NULL) OR
    (parent_thread_id IS NOT NULL AND branch_point_message_id IS NOT NULL)
);

ALTER TABLE threads DROP CONSTRAINT IF EXISTS ck_threads_no_self_parent;
ALTER TABLE threads ADD CONSTRAINT ck_threads_no_self_parent CHECK (
    parent_thread_id IS NULL OR parent_thread_id <> id
);

CREATE INDEX IF NOT EXISTS ix_threads_parent_thread_id
    ON threads (parent_thread_id);
CREATE INDEX IF NOT EXISTS ix_threads_branch_point_message_id
    ON threads (branch_point_message_id);
CREATE INDEX IF NOT EXISTS ix_threads_owner_visible
    ON threads (agent_id, owner_key, archived, updated_at DESC)
    WHERE deleted_by_owner_at IS NULL;

CREATE OR REPLACE FUNCTION fn_threads_branch_scope_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.parent_thread_id IS DISTINCT FROM OLD.parent_thread_id
           OR NEW.branch_point_message_id IS DISTINCT FROM OLD.branch_point_message_id THEN
            RAISE EXCEPTION 'Thread branch lineage pointers are immutable after creation'
                USING ERRCODE = '23514';
        END IF;
        IF (NEW.agent_id IS DISTINCT FROM OLD.agent_id
            OR NEW.owner_key IS DISTINCT FROM OLD.owner_key)
           AND (
               OLD.parent_thread_id IS NOT NULL
               OR EXISTS (SELECT 1 FROM threads c WHERE c.parent_thread_id = OLD.id)
           ) THEN
            RAISE EXCEPTION 'Thread agent_id and owner_key are immutable in a branch lineage'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.parent_thread_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM threads p
             WHERE p.id = NEW.parent_thread_id
               AND p.agent_id = NEW.agent_id
               AND p.owner_key IS NOT DISTINCT FROM NEW.owner_key
        ) THEN
            RAISE EXCEPTION 'Branched thread must share agent_id and owner_key with parent_thread_id'
                USING ERRCODE = '23514';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM messages m
             WHERE m.id = NEW.branch_point_message_id
               AND m.thread_id = NEW.parent_thread_id
        ) THEN
            RAISE EXCEPTION 'branch_point_message_id must belong directly to parent_thread_id'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_threads_branch_scope_guard ON threads;
CREATE TRIGGER trg_threads_branch_scope_guard
BEFORE INSERT OR UPDATE ON threads
FOR EACH ROW
EXECUTE FUNCTION fn_threads_branch_scope_guard();

COMMIT;
