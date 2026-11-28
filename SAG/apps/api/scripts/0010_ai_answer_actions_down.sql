-- MANUAL DESTRUCTIVE ROLLBACK ONLY for `migrations/0010_ai_answer_actions.sql`.
-- Prerequisites BEFORE running:
--   1. Disable answer-actions API routes and roll back dependent application code.
--   2. Export `answer_feedback` and `answer_shares` rows for archival.
--   3. Obtain explicit approval from Thang (never execute automatically).

BEGIN;

DROP INDEX IF EXISTS ix_answer_shares_message;
DROP INDEX IF EXISTS ix_answer_shares_question;
DROP INDEX IF EXISTS ix_answer_shares_scope_created;
DROP INDEX IF EXISTS uq_answer_share_live_creator_message;
DROP TABLE IF EXISTS answer_shares;

DROP INDEX IF EXISTS ix_answer_feedback_rating_created;
DROP INDEX IF EXISTS ix_answer_feedback_scope_created;
DROP INDEX IF EXISTS ix_answer_feedback_tenant_created;
DROP INDEX IF EXISTS ix_answer_feedback_created_id;
DROP TABLE IF EXISTS answer_feedback;

DROP INDEX IF EXISTS ix_messages_regenerated_from_message_id;
DROP INDEX IF EXISTS ix_messages_reply_to_message_id;
ALTER TABLE messages DROP COLUMN IF EXISTS regenerated_from_message_id;
ALTER TABLE messages DROP COLUMN IF EXISTS reply_to_message_id;

COMMIT;
