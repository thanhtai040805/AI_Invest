BEGIN;

SET LOCAL lock_timeout = '10s';

ALTER TABLE "log_counter_thesis"
  ALTER COLUMN "thesis_id" TYPE VARCHAR(64)
  USING "thesis_id"::text;

ALTER TABLE "log_investment_thesis"
  ALTER COLUMN "thesis_id" TYPE VARCHAR(64)
  USING "thesis_id"::text;

ALTER TABLE "cio_resolutions"
  ALTER COLUMN "final_resolution" TYPE VARCHAR(64);

COMMIT;
