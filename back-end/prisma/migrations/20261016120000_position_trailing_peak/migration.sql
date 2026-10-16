ALTER TABLE "positions"
    ADD COLUMN "peak_price" DECIMAL(12,2) NOT NULL DEFAULT 0;

UPDATE "positions" AS p
SET "peak_price" = GREATEST(
    p."avg_price",
    COALESCE((
        SELECT MAX(mdc."high") * 1000
        FROM market_data_daily_calculation AS mdc
        WHERE mdc."ticker" = p."symbol"
          AND mdc."date" >= p."opened_at"::date
          AND mdc."date" <= CURRENT_DATE
    ), p."avg_price")
);
