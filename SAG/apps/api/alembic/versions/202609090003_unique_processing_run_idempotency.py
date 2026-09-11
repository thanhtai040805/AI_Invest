"""Make processing-run idempotency keys database-enforced.

Revision ID: 202609090003
Revises: 202609090002
"""

from __future__ import annotations

from alembic import op


revision = "202609090003"
down_revision = "202609090002"
branch_labels = None
depends_on = None

SCHEMA = "sag"


def upgrade() -> None:
    # Preserve historical duplicate runs for audit, but detach duplicate
    # document-job keys. Stage telemetry deliberately has one row per attempt.
    op.execute(
        f"""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY idempotency_key
                       ORDER BY updated_at DESC, created_at DESC, id DESC
                   ) AS rank
            FROM {SCHEMA}.processing_runs
            WHERE idempotency_key IS NOT NULL AND stage = 'document'
        )
        UPDATE {SCHEMA}.processing_runs AS run
        SET idempotency_key = NULL
        FROM ranked
        WHERE run.id = ranked.id AND ranked.rank > 1
        """
    )
    op.create_index(
        "uq_processing_runs_document_idempotency_key",
        "processing_runs",
        ["idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="stage = 'document'",
    )


def downgrade() -> None:
    op.drop_index("uq_processing_runs_document_idempotency_key", table_name="processing_runs", schema=SCHEMA)
