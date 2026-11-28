-- AlterTable: add nullable parent_session_id to chat_sessions for linked research sessions
ALTER TABLE "chat_sessions"
    ADD COLUMN IF NOT EXISTS "parent_session_id" TEXT;

-- CreateTable: durable cascade delete outbox for SAG user memories
CREATE TABLE IF NOT EXISTS "sag_memory_cascade_outbox" (
    "id" TEXT NOT NULL,
    "session_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "reason" TEXT NOT NULL DEFAULT 'USER_DELETED_SESSION',
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "last_error" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "sag_memory_cascade_outbox_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX IF NOT EXISTS "idx_sag_memory_cascade_outbox_status_created"
    ON "sag_memory_cascade_outbox"("status", "created_at");

-- CreateIndex
CREATE INDEX IF NOT EXISTS "idx_sag_memory_cascade_outbox_user_session"
    ON "sag_memory_cascade_outbox"("user_id", "session_id");
