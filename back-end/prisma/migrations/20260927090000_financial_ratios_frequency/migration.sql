ALTER TABLE "financial_ratios"
  ADD COLUMN "frequency" TEXT NOT NULL DEFAULT 'quarterly';

UPDATE "financial_ratios" r
SET "frequency" = 'yearly'
WHERE EXISTS (
    SELECT 1 FROM "financial_statements" fs
    WHERE fs."symbol" = r."symbol" AND fs."period_end" = r."ratio_date"
      AND fs."frequency" = 'yearly'
)
AND NOT EXISTS (
    SELECT 1 FROM "financial_statements" fs
    WHERE fs."symbol" = r."symbol" AND fs."period_end" = r."ratio_date"
      AND fs."frequency" = 'quarterly'
);

ALTER TABLE "financial_ratios"
  DROP CONSTRAINT "financial_ratios_pkey",
  ADD CONSTRAINT "financial_ratios_pkey" PRIMARY KEY ("symbol", "ratio_date", "frequency");

CREATE INDEX "financial_ratios_pit_idx"
  ON "financial_ratios" ("symbol", "frequency", "published_date", "ratio_date");
