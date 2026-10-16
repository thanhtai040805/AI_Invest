ALTER TABLE "positions"
    ADD COLUMN "peak_price" DECIMAL(12,2) NOT NULL DEFAULT 0;

WITH fills AS (
    SELECT o."user_id", o."symbol", UPPER(e."action") AS action,
           e."shares", e."executed_at",
           SUM(CASE WHEN UPPER(e."action") = 'BUY' THEN e."shares" ELSE -e."shares" END)
               OVER (PARTITION BY o."user_id", o."symbol" ORDER BY e."executed_at", e."order_id") AS net_shares
    FROM order_executions AS e
    JOIN orders AS o ON o."id" = e."order_id"::text
    JOIN positions AS p ON p."user_id" = o."user_id" AND p."symbol" = o."symbol"
    WHERE UPPER(e."action") IN ('BUY', 'SELL', 'SELL_MP')
), last_flat AS (
    SELECT "user_id", "symbol", MAX("executed_at") AS executed_at
    FROM fills
    WHERE net_shares <= 0
    GROUP BY "user_id", "symbol"
), position_start AS (
    SELECT p."id",
           COALESCE(
               MIN(f."executed_at") FILTER (
                   WHERE f.action = 'BUY'
                     AND (lf.executed_at IS NULL OR f."executed_at" >= lf.executed_at)
               )::date,
               p."opened_at"::date
           ) AS start_date
    FROM positions AS p
    LEFT JOIN last_flat AS lf ON lf."user_id" = p."user_id" AND lf."symbol" = p."symbol"
    LEFT JOIN fills AS f ON f."user_id" = p."user_id" AND f."symbol" = p."symbol"
    GROUP BY p."id", p."opened_at"
)
UPDATE "positions" AS p
SET "peak_price" = GREATEST(
    p."avg_price",
    COALESCE((
        SELECT MAX(mdc."high") * 1000
        FROM market_data_daily_calculation AS mdc
        JOIN position_start AS ps ON ps."id" = p."id"
        WHERE mdc."ticker" = p."symbol"
          AND mdc."date" >= ps.start_date
          AND mdc."date" <= CURRENT_DATE
    ), p."avg_price")
);
