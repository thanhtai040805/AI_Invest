CREATE TABLE "ohlcv_intraday_1m" (
    "time" TIMESTAMPTZ(6) NOT NULL,
    "symbol" VARCHAR(16) NOT NULL,
    "open" DECIMAL(16,9) NOT NULL,
    "high" DECIMAL(16,9) NOT NULL,
    "low" DECIMAL(16,9) NOT NULL,
    "close" DECIMAL(16,9) NOT NULL,
    "volume" BIGINT NOT NULL,
    "data_source" VARCHAR(16) NOT NULL DEFAULT 'DNSE',
    "fetched_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ohlcv_intraday_1m_pkey" PRIMARY KEY ("time", "symbol")
);

CREATE INDEX "ohlcv_intraday_1m_symbol_time_idx"
    ON "ohlcv_intraday_1m" ("symbol", "time" DESC);
