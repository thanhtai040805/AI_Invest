BEGIN;
ALTER TABLE canonical_blocks ADD COLUMN IF NOT EXISTS dedup_signature_json JSON;
COMMIT;
