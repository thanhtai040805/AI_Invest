from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import UserDefinedType

from sag_api.core.config import settings

revision = "202609070001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "sag"


class PgVectorDDL(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_kw) -> str:
        if self.dimensions:
            return f"vector({int(self.dimensions)})"
        return "vector"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "issuers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("legal_name", sa.String(512), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        schema=SCHEMA,
    )
    op.create_index("ix_issuers_ticker", "issuers", ["ticker"], unique=True, schema=SCHEMA)

    op.create_table(
        "document_assets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("asset_kind", sa.String(32), nullable=False, server_default="canonical_markdown"),
        sa.Column("object_uri", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False, server_default="text/markdown; charset=utf-8"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("canonical_markdown_sha256", sa.String(64), nullable=True),
        sa.Column("parser_version", sa.String(64), nullable=True),
        sa.Column("canonicalization_version", sa.String(64), nullable=False, server_default="canonical-md-v1"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_document_assets_issuer_id", "document_assets", ["issuer_id"], schema=SCHEMA)
    op.create_index("ix_document_assets_content_sha256", "document_assets", ["content_sha256"], schema=SCHEMA)

    op.create_table(
        "documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("source_id", sa.String(32), nullable=True),
        sa.Column("issuer_id", sa.String(32), nullable=False),
        sa.Column("asset_id", sa.String(32), nullable=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False, server_default="text/markdown; charset=utf-8"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_usage", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_layer", sa.String(16), nullable=True),
        sa.Column("error_stage", sa.String(16), nullable=True),
        sa.Column("sag_source_id", sa.String(64), nullable=True),
        sa.Column("doc_role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("activation_requested", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_quarter", sa.Integer(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("processing_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("structure_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("extraction_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("embedding_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("fact_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage", sa.JSON(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], [f"{SCHEMA}.document_assets.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_documents_issuer_id", "documents", ["issuer_id"], schema=SCHEMA)
    op.create_index("ix_documents_asset_id", "documents", ["asset_id"], schema=SCHEMA)
    op.create_index("ix_documents_source_id", "documents", ["source_id"], schema=SCHEMA)
    op.create_index("ix_documents_role_hash", "documents", ["issuer_id", "doc_role", "content_sha256"], schema=SCHEMA)
    op.create_index(
        "uq_documents_active_role",
        "documents",
        ["issuer_id", "doc_role"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_active = true"),
    )

    _create_document_tree_and_evidence()
    _create_entities()
    _create_fact_like_tables()
    _create_run_tables()


def _create_document_tree_and_evidence() -> None:
    op.create_table(
        "document_tree_nodes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(32), nullable=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("parent_id", sa.String(48), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heading", sa.String(512), nullable=False, server_default=""),
        sa.Column("heading_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("node_kind", sa.String(32), nullable=False, server_default="section"),
        sa.Column("start_line", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("end_line", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("relevance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_tree_nodes_document_order", "document_tree_nodes", ["document_id", "order_index"], schema=SCHEMA)
    op.create_index("ix_tree_nodes_document_node", "document_tree_nodes", ["document_id", "node_id"], unique=True, schema=SCHEMA)
    op.create_index("ix_document_tree_nodes_source_id", "document_tree_nodes", ["source_id"], schema=SCHEMA)

    op.create_table(
        "evidence_spans",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("end_line", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("quote_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_evidence_spans_document_node", "evidence_spans", ["document_id", "node_id"], schema=SCHEMA)
    op.create_index("ix_evidence_spans_quote_hash", "evidence_spans", ["quote_hash"], schema=SCHEMA)


def _create_entities() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("canonical_name", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False, server_default="other"),
        sa.Column("raw_label", sa.String(256), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_entities_issuer_canonical", "entities", ["issuer_id", "canonical_name"], schema=SCHEMA)

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("entity_id", sa.String(32), nullable=False),
        sa.Column("alias", sa.String(512), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="extraction"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["entity_id"], [f"{SCHEMA}.entities.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_entity_aliases_entity_alias", "entity_aliases", ["entity_id", "alias"], schema=SCHEMA)

    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(32), nullable=True),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("raw_text", sa.String(512), nullable=False),
        sa.Column("evidence_span_id", sa.String(32), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], [f"{SCHEMA}.entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_span_id"], [f"{SCHEMA}.evidence_spans.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_entity_mentions_document_node", "entity_mentions", ["document_id", "node_id"], schema=SCHEMA)


def _create_fact_like_tables() -> None:
    op.create_table(
        "facts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("evidence_span_id", sa.String(32), nullable=True),
        sa.Column("fact_type", sa.String(64), nullable=False, server_default="other"),
        sa.Column("semantic_key", sa.String(256), nullable=False, server_default=""),
        sa.Column("label", sa.String(512), nullable=False, server_default=""),
        sa.Column("value_text", sa.String(256), nullable=True),
        sa.Column("value_numeric", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("validation_status", sa.String(32), nullable=False, server_default="VALIDATED"),
        sa.Column("raw_label", sa.String(256), nullable=True),
        sa.Column("taxonomy_candidate", sa.String(256), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_span_id"], [f"{SCHEMA}.evidence_spans.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_facts_document_node", "facts", ["document_id", "node_id"], schema=SCHEMA)
    op.create_index("ix_facts_issuer_semantic_period", "facts", ["issuer_id", "semantic_key", "period_end"], schema=SCHEMA)

    op.create_table(
        "relations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("evidence_span_id", sa.String(32), nullable=True),
        sa.Column("subject_entity_id", sa.String(32), nullable=True),
        sa.Column("object_entity_id", sa.String(32), nullable=True),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("object", sa.String(512), nullable=False),
        sa.Column("relation_type", sa.String(64), nullable=False, server_default="other"),
        sa.Column("amount_vnd", sa.Float(), nullable=True),
        sa.Column("ownership_pct", sa.Float(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("validation_status", sa.String(32), nullable=False, server_default="VALIDATED"),
        sa.Column("raw_label", sa.String(256), nullable=True),
        sa.Column("taxonomy_candidate", sa.String(256), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_span_id"], [f"{SCHEMA}.evidence_spans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_entity_id"], [f"{SCHEMA}.entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["object_entity_id"], [f"{SCHEMA}.entities.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_relations_document_type", "relations", ["document_id", "relation_type"], schema=SCHEMA)
    op.create_index("ix_relations_issuer_validation", "relations", ["issuer_id", "validation_status"], schema=SCHEMA)

    op.create_table(
        "moat_signals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("evidence_span_id", sa.String(32), nullable=True),
        sa.Column("pillar", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(32), nullable=False, server_default="positive"),
        sa.Column("strength", sa.String(32), nullable=False, server_default="weak"),
        sa.Column("durability", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("materiality", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("signal", sa.String(512), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False, server_default="VALIDATED"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_span_id"], [f"{SCHEMA}.evidence_spans.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_moat_signals_issuer_pillar", "moat_signals", ["issuer_id", "pillar"], schema=SCHEMA)

    op.create_table(
        "embedding_chunks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(48), nullable=False),
        sa.Column("evidence_span_id", sa.String(32), nullable=True),
        sa.Column("chunk_id", sa.String(64), nullable=False),
        sa.Column("chunk_kind", sa.String(32), nullable=False, server_default="evidence"),
        sa.Column("ticker", sa.String(32), nullable=True),
        sa.Column("doc_role", sa.String(32), nullable=True),
        sa.Column("period", sa.String(64), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("end_line", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("embedding_text_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("embedding_vector", PgVectorDDL(settings.embedding_dimensions), nullable=True),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(200), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("embedding_version", sa.String(64), nullable=False, server_default="embedding-v2"),
        sa.Column("chunking_version", sa.String(64), nullable=False, server_default="chunking-v2"),
        sa.Column("active_version", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processing_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_span_id"], [f"{SCHEMA}.evidence_spans.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index("ix_embedding_chunks_issuer_active", "embedding_chunks", ["issuer_id", "active_version"], schema=SCHEMA)
    op.create_index("ix_embedding_chunks_document_node", "embedding_chunks", ["document_id", "node_id"], schema=SCHEMA)
    # pgvector HNSW supports vector columns up to 2000 dimensions. Some current
    # deployments use higher-dimensional OpenAI-compatible embedding models
    # (for example 4096-dim Qwen embeddings). Keep storage dimension-locked and
    # skip the ANN index for those deployments instead of silently changing the
    # configured model/dimension.
    if settings.embedding_dimensions is None or settings.embedding_dimensions <= 2000:
        op.execute(
            f"CREATE INDEX ix_embedding_chunks_vector_hnsw "
            f"ON {SCHEMA}.embedding_chunks USING hnsw (embedding_vector vector_cosine_ops) "
            f"WHERE embedding_vector IS NOT NULL"
        )
    op.execute(
        f"CREATE INDEX ix_embedding_chunks_text_trgm "
        f"ON {SCHEMA}.embedding_chunks USING gin ((metadata_json ->> 'text') gin_trgm_ops)"
    )


def _create_run_tables() -> None:
    op.create_table(
        "processing_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("processing_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("stage", sa.String(64), nullable=False, server_default="structure"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_processing_runs_document_version", "processing_runs", ["document_id", "processing_version"], schema=SCHEMA)
    op.create_index("ix_processing_runs_idempotency_key", "processing_runs", ["idempotency_key"], schema=SCHEMA)

    op.create_table(
        "assessment_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=False),
        sa.Column("assessment_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="INSUFFICIENT"),
        sa.Column("policy_version", sa.String(64), nullable=False, server_default="policy-v2"),
        sa.Column("document_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence_digest", sa.String(64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_assessment_runs_issuer_kind", "assessment_runs", ["issuer_id", "assessment_kind"], schema=SCHEMA)

    op.create_table(
        "review_queue",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer_id", sa.String(32), nullable=True),
        sa.Column("document_id", sa.String(32), nullable=True),
        sa.Column("item_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["issuer_id"], [f"{SCHEMA}.issuers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{SCHEMA}.documents.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_review_queue_status", "review_queue", ["status"], schema=SCHEMA)


def downgrade() -> None:
    for table in (
        "review_queue",
        "assessment_runs",
        "processing_runs",
        "embedding_chunks",
        "moat_signals",
        "relations",
        "facts",
        "entity_mentions",
        "entity_aliases",
        "entities",
        "evidence_spans",
        "document_tree_nodes",
        "documents",
        "document_assets",
        "issuers",
    ):
        op.drop_table(table, schema=SCHEMA)
