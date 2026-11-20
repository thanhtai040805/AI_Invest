-- CreateTable
CREATE TABLE IF NOT EXISTS "sag_chat_eval_feedback" (
    "id" TEXT NOT NULL,
    "message_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "verdict" VARCHAR(16) NOT NULL,
    "citation_ok" BOOLEAN NOT NULL DEFAULT true,
    "table_calc_ok" BOOLEAN NOT NULL DEFAULT true,
    "graph_context_ok" BOOLEAN NOT NULL DEFAULT true,
    "hallucination_flag" BOOLEAN NOT NULL DEFAULT false,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "sag_chat_eval_feedback_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX IF NOT EXISTS "uq_sag_chat_eval_feedback_message_user"
    ON "sag_chat_eval_feedback"("message_id", "user_id");

-- CreateIndex
CREATE INDEX IF NOT EXISTS "idx_sag_chat_eval_feedback_message_id"
    ON "sag_chat_eval_feedback"("message_id");

-- CreateIndex
CREATE INDEX IF NOT EXISTS "idx_sag_chat_eval_feedback_user_created"
    ON "sag_chat_eval_feedback"("user_id", "created_at" DESC);

-- AddForeignKey
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'sag_chat_eval_feedback_message_id_fkey'
    ) THEN
        ALTER TABLE "sag_chat_eval_feedback"
            ADD CONSTRAINT "sag_chat_eval_feedback_message_id_fkey"
            FOREIGN KEY ("message_id") REFERENCES "chat_messages"("id")
            ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END $$;

-- AddForeignKey
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'sag_chat_eval_feedback_user_id_fkey'
    ) THEN
        ALTER TABLE "sag_chat_eval_feedback"
            ADD CONSTRAINT "sag_chat_eval_feedback_user_id_fkey"
            FOREIGN KEY ("user_id") REFERENCES "users"("id")
            ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END $$;
