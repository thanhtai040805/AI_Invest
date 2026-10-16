ALTER TABLE "orders"
    ADD COLUMN "decision_context" JSONB;

ALTER TABLE "order_executions"
    ADD COLUMN "decision_context" JSONB;

ALTER TABLE "stop_loss_events"
    ADD COLUMN "decision_context" JSONB;
