CREATE TABLE "market_data_quote_snapshots" (
    "replay_at" TIMESTAMPTZ(6) NOT NULL,
    "symbol" VARCHAR(16) NOT NULL,
    "quote_time" TIMESTAMPTZ(6) NOT NULL,
    "board_id" VARCHAR(8) NOT NULL,
    "bid" JSONB NOT NULL,
    "offer" JSONB NOT NULL,
    "total_bid_quantity" BIGINT,
    "total_offer_quantity" BIGINT,
    "data_source" VARCHAR(16) NOT NULL DEFAULT 'DNSE',
    "fetched_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "market_data_quote_snapshots_quote_before_replay_check"
        CHECK ("quote_time" <= "replay_at"),
    CONSTRAINT "market_data_quote_snapshots_pkey" PRIMARY KEY ("symbol", "replay_at")
);
