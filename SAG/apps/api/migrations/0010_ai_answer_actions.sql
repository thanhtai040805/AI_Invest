-- AI answer actions: additive PostgreSQL schema. No legacy parent backfill.
-- Apply before dependent answer-actions API deployment. Never run a destructive
-- rollback without exporting feedback/shares and explicit owner approval.
-- Manual rollback script: `SAG/apps/api/scripts/0010_ai_answer_actions_down.sql`.
BEGIN;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_message_id VARCHAR(32)
    REFERENCES messages(id) ON DELETE SET NULL;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS regenerated_from_message_id VARCHAR(32)
    REFERENCES messages(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_messages_reply_to_message_id
    ON messages (reply_to_message_id);
CREATE INDEX IF NOT EXISTS ix_messages_regenerated_from_message_id
    ON messages (regenerated_from_message_id);

CREATE TABLE IF NOT EXISTS answer_feedback (
    id VARCHAR(36) PRIMARY KEY,
    message_id VARCHAR(32) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    voter_subject VARCHAR(256) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    rating VARCHAR(8) NOT NULL,
    reason_category VARCHAR(32),
    reason_text TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_answer_feedback_message_voter UNIQUE (message_id, voter_subject),
    CONSTRAINT ck_answer_feedback_rating CHECK (rating IN ('LIKE', 'DISLIKE')),
    CONSTRAINT ck_answer_feedback_reason CHECK (
        (rating = 'LIKE' AND reason_category IS NULL AND reason_text IS NULL) OR
        (rating = 'DISLIKE' AND reason_category IS NOT NULL AND reason_category IN
            ('INACCURATE', 'OUTDATED', 'IRRELEVANT_CITATION', 'INCOMPLETE', 'OTHER') AND
            (reason_category != 'OTHER' OR reason_text IS NOT NULL))
    ),
    CONSTRAINT ck_answer_feedback_reason_text CHECK (
        reason_text IS NULL OR (length(trim(reason_text)) > 0 AND length(reason_text) <= 1000)
    ),
    CONSTRAINT ck_answer_feedback_tenant CHECK (length(trim(tenant_id)) > 0),
    CONSTRAINT ck_answer_feedback_project CHECK (length(trim(project_id)) > 0),
    CONSTRAINT ck_answer_feedback_voter CHECK (length(trim(voter_subject)) > 0)
);
CREATE INDEX IF NOT EXISTS ix_answer_feedback_created_id
    ON answer_feedback (created_at, id);
CREATE INDEX IF NOT EXISTS ix_answer_feedback_tenant_created
    ON answer_feedback (tenant_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_answer_feedback_scope_created
    ON answer_feedback (tenant_id, project_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_answer_feedback_rating_created
    ON answer_feedback (rating, created_at, id);

CREATE TABLE IF NOT EXISTS answer_shares (
    id VARCHAR(64) PRIMARY KEY,
    creator_subject VARCHAR(256) NOT NULL,
    message_id VARCHAR(32) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    question_id VARCHAR(32) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(64) NOT NULL,
    question_content TEXT NOT NULL,
    answer_content TEXT NOT NULL,
    citations JSON NOT NULL DEFAULT '[]',
    source_ids JSON NOT NULL DEFAULT '[]',
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_answer_share_tenant CHECK (length(trim(tenant_id)) > 0),
    CONSTRAINT ck_answer_share_project CHECK (length(trim(project_id)) > 0),
    CONSTRAINT ck_answer_share_creator CHECK (length(trim(creator_subject)) > 0),
    CONSTRAINT ck_answer_share_distinct_messages CHECK (message_id != question_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_answer_share_live_creator_message
    ON answer_shares (creator_subject, message_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_answer_shares_scope_created
    ON answer_shares (tenant_id, project_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_answer_shares_question ON answer_shares (question_id);
CREATE INDEX IF NOT EXISTS ix_answer_shares_message ON answer_shares (message_id);

COMMIT;
