-- Normalize legacy Shadow & Replay orders stored in naive Asia/Ho_Chi_Minh wall time (hours 09..15) to naive UTC (hours 02..08)
UPDATE "orders"
SET "created_at" = "created_at" - INTERVAL '7 hours'
WHERE "order_type" IN ('SHADOW_LIMIT', 'SHADOW_ML_LIMIT', 'REPLAY_MARKET', 'REPLAY_ML_LIMIT', 'REPLAY_ML_STOP')
  AND EXTRACT(HOUR FROM "created_at") >= 9;

-- Normalize any legacy positions stored in naive Asia/Ho_Chi_Minh wall time to naive UTC
UPDATE "positions"
SET "opened_at" = "opened_at" - INTERVAL '7 hours'
WHERE EXTRACT(HOUR FROM "opened_at") >= 9;
