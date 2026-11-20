-- PostgreSQL forward schema for Project -> Source authorization mappings.
-- Apply with SAG API stopped or under the repository's approved production
-- migration window. Existing Sources receive no mapping and remain inaccessible.
BEGIN;

CREATE TABLE IF NOT EXISTS source_project_mappings (
    id VARCHAR(32) PRIMARY KEY,
    source_id VARCHAR(32) NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    organization_id VARCHAR(256) NOT NULL,
    project_id VARCHAR(256) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    mapping_version INTEGER NOT NULL DEFAULT 1,
    confirmed_at TIMESTAMPTZ NULL,
    confirmed_by VARCHAR(256) NULL,
    approval_ref VARCHAR(256) NULL,
    batch_id VARCHAR(64) NULL,
    input_sha256 VARCHAR(64) NULL,
    revoked_at TIMESTAMPTZ NULL,
    revoked_by VARCHAR(256) NULL,
    revocation_ref VARCHAR(256) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_source_project_mappings_state
        CHECK (state IN ('PENDING', 'CONFIRMED', 'REVOKED')),
    CONSTRAINT ck_source_project_mappings_organization_id
        CHECK (length(trim(organization_id)) > 0),
    CONSTRAINT ck_source_project_mappings_project_id
        CHECK (length(trim(project_id)) > 0),
    CONSTRAINT ck_source_project_mappings_mapping_version
        CHECK (mapping_version > 0),
    CONSTRAINT ck_source_project_mappings_confirmation
        CHECK (state != 'CONFIRMED' OR
            (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL AND approval_ref IS NOT NULL)),
    CONSTRAINT ck_source_project_mappings_revocation
        CHECK (state != 'REVOKED' OR
            (revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revocation_ref IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_source_project_mappings_source_id
    ON source_project_mappings (source_id);
CREATE INDEX IF NOT EXISTS ix_source_project_mappings_scope
    ON source_project_mappings (organization_id, project_id, state, source_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_project_mappings_current_source
    ON source_project_mappings (source_id)
    WHERE state IN ('PENDING', 'CONFIRMED');

COMMIT;
