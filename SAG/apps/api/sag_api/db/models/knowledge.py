"""Phase 5 truth and immutable graph/routing candidates; independent of SearchUnit."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, UTCDateTime


class KnowledgeUnit(Base):
    __tablename__ = "knowledge_units"
    __table_args__ = (Index("idx_knowledge_units_scope", "tenant_id", "project_id", "security_partition_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(128))
    security_partition_id: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    input_checksum: Mapped[str] = mapped_column(String(64))
    checksum: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    # Locator, source snapshot, clocks and extractor configuration are frozen here.
    provenance_json: Mapped[dict] = mapped_column(JSON)
    features_json: Mapped[dict] = mapped_column(JSON)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_to: Mapped[datetime] = mapped_column(UTCDateTime())


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("knowledge_units.id", ondelete="CASCADE"), index=True)
    tier: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
    extractor_version: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[float] = mapped_column(Float)
    # Quote, exact block/span, locator and candidate semantics, never an unanchored fact.
    payload_json: Mapped[dict] = mapped_column(JSON)


class KnowledgeJob(Base):
    __tablename__ = "knowledge_jobs"
    __table_args__ = (Index("idx_knowledge_jobs_dispatch", "status", "available_at", "priority"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    # Keep validated enrichment artifacts when the derived KnowledgeUnit store is rebuilt.
    unit_id: Mapped[str | None] = mapped_column(String(36))
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tree_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    input_checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    error_code: Mapped[str | None] = mapped_column(String(64))


class KnowledgeQueueControl(Base):
    __tablename__ = "knowledge_queue_control"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tokens_reserved: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeGraphBuild(Base):
    __tablename__ = "knowledge_graph_builds"
    __table_args__ = (Index("ix_knowledge_graph_input", "tenant_id", "project_id", "input_fingerprint", unique=True),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    checksum: Mapped[str] = mapped_column(String(64))
    manifest_json: Mapped[dict] = mapped_column(JSON)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


class KnowledgeUnitEdge(Base):
    __tablename__ = "knowledge_unit_edges"

    build_id: Mapped[str] = mapped_column(ForeignKey("knowledge_graph_builds.id", ondelete="CASCADE"), primary_key=True)
    source_unit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_unit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    security_partition_id: Mapped[str] = mapped_column(String(64))
    weight: Mapped[float] = mapped_column(Float)
    signals_json: Mapped[dict] = mapped_column(JSON)


class KnowledgeTreeNode(Base):
    __tablename__ = "knowledge_tree_nodes"

    tree_version: Mapped[str] = mapped_column(
        ForeignKey("tree_manifests.tree_version", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(128))
    security_partition_id: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
