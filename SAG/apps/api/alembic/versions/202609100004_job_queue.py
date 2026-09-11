"""Add the persistent job table used by the in-process OCR queue.

The financial v2 migrations create the document/evidence tables, but the
runtime queue model was left out of the migration chain.  Keep this table
independent from the legacy ``sources`` table: OCR_FROM_URL and
OCR_FROM_OBJECT jobs intentionally start without a document or source row.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202609100004"
down_revision = "202609090003"
branch_labels = None
depends_on = None

SCHEMA = "sag"


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("source_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_jobs_status", "jobs", ["status"], schema=SCHEMA)
    op.create_index("ix_jobs_source_id", "jobs", ["source_id"], schema=SCHEMA)
    op.create_index("ix_jobs_document_id", "jobs", ["document_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_jobs_document_id", table_name="jobs", schema=SCHEMA)
    op.drop_index("ix_jobs_source_id", table_name="jobs", schema=SCHEMA)
    op.drop_index("ix_jobs_status", table_name="jobs", schema=SCHEMA)
    op.drop_table("jobs", schema=SCHEMA)
