"""Add open evidence-backed observations and document content facets.

Revision ID: 202609090002
Revises: 202609070001
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202609090002"
down_revision = "202609070001"
branch_labels = None
depends_on = None

SCHEMA = "sag"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "document_facets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("evidence_span_id", sa.String(32), nullable=True),
        sa.Column("facet", sa.String(128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="llm"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_span_id"], [f"{SCHEMA}.evidence_spans.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_document_facets_document_facet", "document_facets", ["document_id", "facet"], schema=SCHEMA)
    op.create_index("ix_document_facets_issuer_facet", "document_facets", ["issuer_id", "facet"], schema=SCHEMA)

    op.create_table(
        "observations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("evidence_span_id", sa.String(32), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(512), nullable=True),
        sa.Column("predicate", sa.String(256), nullable=False, server_default="observation"),
        sa.Column("object", sa.String(512), nullable=True),
        sa.Column("topic_tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("attributes_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("normalization_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_span_id"], [f"{SCHEMA}.evidence_spans.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_observations_document_node", "observations", ["document_id", "node_id"], schema=SCHEMA)
    op.create_index("ix_observations_issuer_predicate", "observations", ["issuer_id", "predicate"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_observations_issuer_predicate", table_name="observations", schema=SCHEMA)
    op.drop_index("ix_observations_document_node", table_name="observations", schema=SCHEMA)
    op.drop_table("observations", schema=SCHEMA)
    op.drop_index("ix_document_facets_issuer_facet", table_name="document_facets", schema=SCHEMA)
    op.drop_index("ix_document_facets_document_facet", table_name="document_facets", schema=SCHEMA)
    op.drop_table("document_facets", schema=SCHEMA)
