-- Hard migration: thay thế lớp Economic Moat bằng Business Quality.
-- Database mới chưa có dữ liệu production nên không giữ compatibility alias.

DROP TABLE IF EXISTS moat_profiles;

CREATE TABLE IF NOT EXISTS business_quality_profiles (
    ticker VARCHAR(16) PRIMARY KEY,
    fiscal_year INTEGER NOT NULL DEFAULT 2025,
    report_type VARCHAR(32) NOT NULL DEFAULT 'ANNUAL_REPORT',
    quality_score NUMERIC(6,2),
    quality_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_sag_doc_id VARCHAR(128),
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_stale BOOLEAN DEFAULT FALSE
);

DROP INDEX IF EXISTS idx_moat_profiles_ticker;
CREATE INDEX IF NOT EXISTS idx_business_quality_profiles_ticker
    ON business_quality_profiles (ticker);

ALTER TABLE log_equity_research
    DROP COLUMN IF EXISTS moat_citations_evidence;

ALTER TABLE log_equity_research
    ADD COLUMN IF NOT EXISTS business_quality_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
