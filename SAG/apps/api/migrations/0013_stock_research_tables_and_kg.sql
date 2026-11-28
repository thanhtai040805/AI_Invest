-- ============================================================================
-- Migration 0013: Stock Research Extracted Tables & Zero-Hardcode 3-Layer KG
-- Idempotent PostgreSQL 16 DDL matching Cross-Track Contract 1.
-- ============================================================================

BEGIN;

-- 1. Dual-Representation Normalized Financial Tables
CREATE TABLE IF NOT EXISTS sag_extracted_tables (
    table_id             VARCHAR(64) PRIMARY KEY,
    project_id           VARCHAR(32) NOT NULL,
    document_version_id  VARCHAR(64) NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    sub_doc_slot         VARCHAR(32),
    fiscal_year          SMALLINT,
    fiscal_quarter       SMALLINT DEFAULT 0,
    table_title          TEXT,
    section_path         TEXT,
    page_from            INT,
    page_to              INT,
    currency             VARCHAR(16) DEFAULT 'VND',
    unit_raw             VARCHAR(64),
    unit_multiplier      DOUBLE PRECISION DEFAULT 1.0,
    headers_json         JSONB NOT NULL,
    rows_json            JSONB NOT NULL,
    footnotes_text       TEXT,
    sanity_error_ratio   DOUBLE PRECISION,
    verification_status  VARCHAR(24) NOT NULL,
    table_hash           CHAR(64) NOT NULL,
    valid_from           TIMESTAMPTZ NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sag_extracted_tables_pit
    ON sag_extracted_tables (project_id, valid_from, sub_doc_slot);
CREATE INDEX IF NOT EXISTS idx_sag_extracted_tables_doc_ver
    ON sag_extracted_tables (document_version_id);

-- 2. 3-Layer Knowledge Graph Nodes (PIT)
CREATE TABLE IF NOT EXISTS kg_nodes (
    node_id        VARCHAR(128) PRIMARY KEY,
    node_type      VARCHAR(32) NOT NULL,
    label          TEXT NOT NULL,
    ticker         VARCHAR(32),
    metadata_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    effective_from DATE NOT NULL,
    valid_to       DATE NOT NULL DEFAULT '9999-12-31',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_type_ticker_pit
    ON kg_nodes (node_type, ticker, effective_from, valid_to);

-- 3. 3-Layer Knowledge Graph Directed Edges (PIT)
CREATE TABLE IF NOT EXISTS kg_edges (
    edge_id           VARCHAR(64) PRIMARY KEY,
    src_node_id       VARCHAR(128) NOT NULL REFERENCES kg_nodes(node_id) ON DELETE CASCADE,
    dst_node_id       VARCHAR(128) NOT NULL REFERENCES kg_nodes(node_id) ON DELETE CASCADE,
    relation_type     VARCHAR(48) NOT NULL,
    layer             VARCHAR(16) NOT NULL,
    direction         VARCHAR(24),
    weight            DOUBLE PRECISION,
    beta              DOUBLE PRECISION,
    p_value           DOUBLE PRECISION,
    fdr_q_value       DOUBLE PRECISION,
    tercile_stable    BOOLEAN,
    verification_tier VARCHAR(24) NOT NULL,
    evidence_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    effective_from    DATE NOT NULL,
    valid_to          DATE NOT NULL DEFAULT '9999-12-31',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_src_pit
    ON kg_edges (src_node_id, effective_from, valid_to, layer, relation_type);
CREATE INDEX IF NOT EXISTS idx_kg_edges_dst_pit
    ON kg_edges (dst_node_id, effective_from, valid_to, layer, relation_type);

-- 4. Quantitative Macro & Cross-Sector Sensitivity Snapshots (PIT)
CREATE TABLE IF NOT EXISTS macro_sensitivity_snapshots (
    snapshot_id       VARCHAR(64) PRIMARY KEY,
    target_id         VARCHAR(64) NOT NULL,
    target_level      VARCHAR(16) NOT NULL,
    macro_factor      VARCHAR(64) NOT NULL,
    as_of_date        DATE NOT NULL,
    best_lag_q        SMALLINT NOT NULL DEFAULT 0,
    beta              DOUBLE PRECISION NOT NULL,
    hac_t_stat        DOUBLE PRECISION,
    p_value           DOUBLE PRECISION,
    fdr_q_value       DOUBLE PRECISION,
    tercile_stable    BOOLEAN NOT NULL DEFAULT FALSE,
    doc_stance        VARCHAR(24),
    verification_tier VARCHAR(24) NOT NULL,
    effective_from    DATE NOT NULL,
    valid_to          DATE NOT NULL DEFAULT '9999-12-31',
    sample_n          INT,
    metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_macro_sensitivity_pit
    ON macro_sensitivity_snapshots (target_id, target_level, macro_factor, effective_from, valid_to, as_of_date);

COMMIT;
