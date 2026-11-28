from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, new_id
from sag_api.enums import DocumentStatus


class Document(IDMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_source_sag_source", "source_id", "sag_source_id"),
        Index("ix_documents_source_active_created", "source_id", "is_active", "created_at"),
        Index("ix_documents_tenant_project_logical", "tenant_id", "project_id", "logical_source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)

    tenant_id: Mapped[str] = mapped_column(
        String(64), default="tenant_continuum_default", server_default="tenant_continuum_default"
    )
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    logical_source_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=True
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
    token_usage_breakdown: Mapped[dict | None] = mapped_column("token_usage_json", JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Failure attribution: responsibility layer (api/engine/llm/store) and pipeline stage (parse/chunk/extract/...),
    # facilitating debugging directly from export logs. Populated only when status=failed.
    error_layer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # zleap-sag ingest returned source_id (for provenance tracking)
    sag_source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # OCTX updates are staged in shadow installation; final cutover exposes only the new document revision.
    octx_installation_id: Mapped[str | None] = mapped_column(
        ForeignKey("octx_installations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    octx_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    parser_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mineru_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mineru_model: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parser_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fallback_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Identity of the embedding configuration that produced this document's
    # current vectors.  The endpoint is represented only by a one-way
    # fingerprint; credentials and the raw URL are never persisted.
    vector_identity: Mapped[dict | None] = mapped_column(
        "vector_identity_json",
        JSON,
        nullable=True,
    )


