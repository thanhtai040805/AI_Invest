-- Preserve the decimal precision returned by DNSE for adjusted historical OHLC bars.
ALTER TABLE "ohlcv"
    ALTER COLUMN "open" TYPE DECIMAL(16,9),
    ALTER COLUMN "high" TYPE DECIMAL(16,9),
    ALTER COLUMN "low" TYPE DECIMAL(16,9),
    ALTER COLUMN "close" TYPE DECIMAL(16,9);
