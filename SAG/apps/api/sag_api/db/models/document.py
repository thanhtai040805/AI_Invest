from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Date, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.core.config import settings
from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime
from sag_api.db.vector_type import PgVector
from sag_api.enums import DocumentRole, DocumentStatus, ProcessingStageStatus


class Issuer(IDMixin, TimestampMixin, Base):
    __tablename__ = "issuers"
    __table_args__ = (Index("ix_issuers_ticker", "ticker", unique=True),)

    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentAsset(IDMixin, TimestampMixin, Base):
    __tablename__ = "document_assets"

    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    asset_kind: Mapped[str] = mapped_column(String(32), default="canonical_markdown")
    object_uri: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(128), default="text/markdown; charset=utf-8")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    canonical_markdown_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonicalization_version: Mapped[str] = mapped_column(String(64), default="canonical-md-v1")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(IDMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_source_sag_source", "source_id", "sag_source_id"),
        Index("ix_documents_source_role_active", "source_id", "doc_role", "is_active"),
        Index("ix_documents_source_role_hash", "source_id", "doc_role", "content_sha256"),
    )

    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=True, index=True
    )
    issuer_id: Mapped[str | None] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, native_enum=False, length=16), default=DocumentStatus.PENDING
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phân định lỗi: tầng trách nhiệm (api/engine/llm/store) và khâu trong chuỗi (parse/chunk/extract/...),
    # giúp nghiên cứu phát triển định vị trực tiếp từ log xuất ra. Chỉ có giá trị khi status=failed.
    error_layer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # source_id do zleap-sag ingest trả về (dùng để truy vết)
    sag_source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Phân loại vai trò tài liệu trong Cửa sổ trượt (IOS v5.1 Sliding Window)
    doc_role: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    activation_requested: Mapped[bool] = mapped_column(Boolean, default=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    fiscal_quarter: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    period_start: Mapped[object | None] = mapped_column(Date, nullable=True, default=None)
    period_end: Mapped[object | None] = mapped_column(Date, nullable=True, default=None)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    processing_version: Mapped[int] = mapped_column(Integer, default=1)
    structure_status: Mapped[str] = mapped_column(String(32), default=ProcessingStageStatus.PENDING.value)
    extraction_status: Mapped[str] = mapped_column(String(32), default=ProcessingStageStatus.PENDING.value)
    embedding_status: Mapped[str] = mapped_column(String(32), default=ProcessingStageStatus.PENDING.value)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)


class DocumentTreeNode(IDMixin, TimestampMixin, Base):
    __tablename__ = "document_tree_nodes"
    __table_args__ = (
        Index("ix_tree_nodes_document_order", "document_id", "order_index"),
        Index("ix_tree_nodes_document_node", "document_id", "node_id", unique=True),
    )

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=True, index=True)
    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    node_id: Mapped[str] = mapped_column(String(48))
    parent_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    heading: Mapped[str] = mapped_column(String(512), default="")
    heading_path: Mapped[str] = mapped_column(Text, default="")
    node_kind: Mapped[str] = mapped_column(String(32), default="section")
    start_line: Mapped[int] = mapped_column(Integer, default=1)
    end_line: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class EvidenceSpan(IDMixin, TimestampMixin, Base):
    __tablename__ = "evidence_spans"
    __table_args__ = (Index("ix_evidence_spans_document_node", "document_id", "node_id"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    start_line: Mapped[int] = mapped_column(Integer, default=1)
    end_line: Mapped[int] = mapped_column(Integer, default=1)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_hash: Mapped[str] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Entity(IDMixin, TimestampMixin, Base):
    __tablename__ = "entities"
    __table_args__ = (Index("ix_entities_issuer_canonical", "issuer_id", "canonical_name"),)

    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    canonical_name: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(512))
    entity_type: Mapped[str] = mapped_column(String(64), default="other")
    raw_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class EntityAlias(IDMixin, TimestampMixin, Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (Index("ix_entity_aliases_entity_alias", "entity_id", "alias"),)

    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(64), default="extraction")


class EntityMention(IDMixin, TimestampMixin, Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (Index("ix_entity_mentions_document_node", "document_id", "node_id"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    raw_text: Mapped[str] = mapped_column(String(512))
    evidence_span_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_spans.id", ondelete="SET NULL"), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Fact(IDMixin, TimestampMixin, Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_document_node", "document_id", "node_id"),
        Index("ix_facts_issuer_semantic_period", "issuer_id", "semantic_key", "period_end"),
    )

    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    evidence_span_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_spans.id", ondelete="SET NULL"), nullable=True, index=True)
    fact_type: Mapped[str] = mapped_column(String(64), default="other")
    semantic_key: Mapped[str] = mapped_column(String(256), default="")
    label: Mapped[str] = mapped_column(String(512), default="")
    value_text: Mapped[str | None] = mapped_column(String(256), nullable=True)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    period_start: Mapped[object | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[object | None] = mapped_column(Date, nullable=True)
    as_of: Mapped[object | None] = mapped_column(Date, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), default="VALIDATED")
    raw_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    taxonomy_candidate: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Relation(IDMixin, TimestampMixin, Base):
    __tablename__ = "relations"
    __table_args__ = (
        Index("ix_relations_document_type", "document_id", "relation_type"),
        Index("ix_relations_issuer_validation", "issuer_id", "validation_status"),
    )

    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    evidence_span_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_spans.id", ondelete="SET NULL"), nullable=True, index=True)
    subject_entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"), nullable=True)
    object_entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"), nullable=True)
    subject: Mapped[str] = mapped_column(String(512))
    object: Mapped[str] = mapped_column(String(512))
    relation_type: Mapped[str] = mapped_column(String(64), default="other")
    amount_vnd: Mapped[float | None] = mapped_column(Float, nullable=True)
    ownership_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    period_start: Mapped[object | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[object | None] = mapped_column(Date, nullable=True)
    as_of: Mapped[object | None] = mapped_column(Date, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), default="VALIDATED")
    raw_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    taxonomy_candidate: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class MoatSignal(IDMixin, TimestampMixin, Base):
    __tablename__ = "moat_signals"
    __table_args__ = (Index("ix_moat_signals_issuer_pillar", "issuer_id", "pillar"),)

    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    evidence_span_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_spans.id", ondelete="SET NULL"), nullable=True)
    pillar: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(32), default="positive")
    strength: Mapped[str] = mapped_column(String(32), default="weak")
    durability: Mapped[str] = mapped_column(String(32), default="unknown")
    materiality: Mapped[str] = mapped_column(String(32), default="unknown")
    signal: Mapped[str] = mapped_column(String(512))
    validation_status: Mapped[str] = mapped_column(String(32), default="VALIDATED")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class EmbeddingChunk(IDMixin, TimestampMixin, Base):
    __tablename__ = "embedding_chunks"
    __table_args__ = (
        Index("ix_embedding_chunks_issuer_active", "issuer_id", "active_version"),
        Index("ix_embedding_chunks_document_node", "document_id", "node_id"),
    )

    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    evidence_span_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_spans.id", ondelete="SET NULL"), nullable=True)
    chunk_id: Mapped[str] = mapped_column(String(64), index=True)
    chunk_kind: Mapped[str] = mapped_column(String(32), default="evidence")
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    doc_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, default=1)
    end_line: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding_text_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding_vector: Mapped[list | None] = mapped_column(PgVector(settings.embedding_dimensions), nullable=True)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_version: Mapped[str] = mapped_column(String(64), default="embedding-v2")
    chunking_version: Mapped[str] = mapped_column(String(64), default="chunking-v2")
    active_version: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_version: Mapped[int] = mapped_column(Integer, default=2)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ProcessingRun(IDMixin, TimestampMixin, Base):
    __tablename__ = "processing_runs"
    __table_args__ = (Index("ix_processing_runs_document_version", "document_id", "processing_version"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    processing_version: Mapped[int] = mapped_column(Integer, default=2)
    stage: Mapped[str] = mapped_column(String(64), default="structure")
    status: Mapped[str] = mapped_column(String(32), default=ProcessingStageStatus.PENDING.value)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[object | None] = mapped_column(UTCDateTime(), nullable=True)
    heartbeat_at: Mapped[object | None] = mapped_column(UTCDateTime(), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssessmentRun(IDMixin, TimestampMixin, Base):
    __tablename__ = "assessment_runs"
    __table_args__ = (Index("ix_assessment_runs_issuer_kind", "issuer_id", "assessment_kind"),)

    issuer_id: Mapped[str] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), index=True)
    assessment_kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="INSUFFICIENT")
    policy_version: Mapped[str] = mapped_column(String(64), default="policy-v2")
    document_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ReviewQueueItem(IDMixin, TimestampMixin, Base):
    __tablename__ = "review_queue"
    __table_args__ = (Index("ix_review_queue_status", "status"),)

    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    item_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    reason: Mapped[str] = mapped_column(String(512), default="")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentEntity(IDMixin, TimestampMixin, Base):
    __tablename__ = "document_entities"
    __table_args__ = (Index("ix_document_entities_document_canonical", "document_id", "canonical_name"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(512))
    entity_type: Mapped[str] = mapped_column(String(64), default="COMPANY")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentFact(IDMixin, TimestampMixin, Base):
    __tablename__ = "document_facts"
    __table_args__ = (Index("ix_document_facts_document_node", "document_id", "node_id"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    fact_type: Mapped[str] = mapped_column(String(64), default="OTHER")
    label: Mapped[str] = mapped_column(String(512), default="")
    value: Mapped[str | None] = mapped_column(String(256), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, default=1)
    end_line: Mapped[int] = mapped_column(Integer, default=1)
    quote_hash: Mapped[str] = mapped_column(String(64), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentRelation(IDMixin, TimestampMixin, Base):
    __tablename__ = "document_relations"
    __table_args__ = (Index("ix_document_relations_document_type", "document_id", "relation_type"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    subject: Mapped[str] = mapped_column(String(512))
    object: Mapped[str] = mapped_column(String(512))
    relation_type: Mapped[str] = mapped_column(String(64))
    amount_vnd: Mapped[float | None] = mapped_column(Float, nullable=True)
    ownership_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, default=1)
    end_line: Mapped[int] = mapped_column(Integer, default=1)
    quote_hash: Mapped[str] = mapped_column(String(64), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentEvidenceChunk(IDMixin, TimestampMixin, Base):
    __tablename__ = "document_evidence_chunks"
    __table_args__ = (
        Index("ix_document_evidence_document_node", "document_id", "node_id"),
        Index("ix_document_evidence_active_role", "source_id", "doc_role", "active_version"),
    )

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(48), index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), index=True)
    chunk_kind: Mapped[str] = mapped_column(String(32), default="evidence")
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    doc_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, default=1)
    end_line: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding_text_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_version: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_version: Mapped[int] = mapped_column(Integer, default=2)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
