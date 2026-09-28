-- Keep a fresh migrate deploy aligned with the current Prisma datamodel.
DROP INDEX IF EXISTS "financial_ratios_pit_idx";

ALTER TABLE "bctc_pipeline_records"
  ADD COLUMN IF NOT EXISTS "ocr_cache_version" VARCHAR(32),
  ADD COLUMN IF NOT EXISTS "source_document_id" BIGINT;

ALTER TABLE "cio_strategic_directives"
  ALTER COLUMN "status" TYPE VARCHAR(32),
  ALTER COLUMN "strategic_cash_target_pct" TYPE DECIMAL(6,2);

DO $$
DECLARE
  has_legacy_column BOOLEAN;
  has_current_column BOOLEAN;
  has_rows BOOLEAN;
BEGIN
  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'log_equity_research'
      AND column_name = 'moat_citations_evidence'
  ) INTO has_legacy_column;
  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'log_equity_research'
      AND column_name = 'business_quality_evidence'
  ) INTO has_current_column;

  IF has_legacy_column AND NOT has_current_column THEN
    ALTER TABLE "log_equity_research"
      RENAME COLUMN "moat_citations_evidence" TO "business_quality_evidence";
  ELSIF has_legacy_column AND has_current_column THEN
    IF EXISTS (
      SELECT 1 FROM "log_equity_research"
      WHERE "business_quality_evidence" IS DISTINCT FROM '{}'::jsonb
        AND "moat_citations_evidence" IS DISTINCT FROM "business_quality_evidence"
    ) THEN
      RAISE EXCEPTION 'Conflicting evidence columns in log_equity_research; archive before schema sync';
    END IF;
    UPDATE "log_equity_research"
    SET "business_quality_evidence" = "moat_citations_evidence"
    WHERE "business_quality_evidence" = '{}'::jsonb
      AND "moat_citations_evidence" IS NOT NULL;
    ALTER TABLE "log_equity_research" DROP COLUMN "moat_citations_evidence";
  END IF;

  IF NOT has_current_column AND NOT has_legacy_column THEN
    ALTER TABLE "log_equity_research"
      ADD COLUMN "business_quality_evidence" JSONB NOT NULL DEFAULT '{}';
  END IF;

  ALTER TABLE "log_equity_research"
    ALTER COLUMN "business_quality_evidence" SET DEFAULT '{}',
    ALTER COLUMN "business_quality_evidence" SET NOT NULL;

  IF to_regclass('public.market_data_quote_snapshots') IS NOT NULL THEN
    EXECUTE 'SELECT EXISTS (SELECT 1 FROM public.market_data_quote_snapshots)' INTO has_rows;
    IF has_rows THEN
      RAISE EXCEPTION 'market_data_quote_snapshots contains rows but is absent from Prisma; archive before schema sync';
    END IF;
    DROP TABLE "market_data_quote_snapshots";
  END IF;
END $$;
