"""Database models for SAG Knowledge Routing RAG (Phase 0/1+ Foundations).

Defined in accordance with PostgreSQL 16 DDL specification in
phase-0-contracts-and-foundations.md (Pillar 2).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, UTCDateTime


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_document_versions_doc_ver"),
        Index("idx_document_versions_hash", "file_hash"),
        Index("idx_document_versions_temporal", "valid_from", "valid_to"),
        Index("idx_document_versions_search_status", "search_status"),
        Index("idx_document_versions_knowledge_status", "knowledge_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    source_published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=lambda: datetime.now(UTC), server_default=func.now(), nullable=False
    )
    ingested_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    valid_to: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), default="RECEIVED", nullable=False)
    search_status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    knowledge_status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED", nullable=False)
    search_ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    knowledge_ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "project_id", "idempotency_key",
            name="uq_ingestion_runs_tenant_project_idempotency"
        ),
        Index("idx_ingestion_runs_tenant_project", "tenant_id", "project_id"),
        Index("idx_ingestion_runs_content_hash", "tenant_id", "project_id", "content_hash"),
        Index("idx_ingestion_runs_research_task", "research_task_id"),
        Index("idx_ingestion_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    research_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True
    )
    current_stage: Mapped[str] = mapped_column(String(32), default="RECEIVE", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error_layer: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class UploadIdempotencyKey(Base):
    """Bind retry keys to the original run, including content-duplicate uploads."""

    __tablename__ = "upload_idempotency_keys"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )


class StageRun(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (
        Index("idx_stage_runs_run_stage", "run_id", "stage"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class CanonicalBlock(Base):
    __tablename__ = "canonical_blocks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_canonical_blocks_ordinal"),
        Index("idx_canonical_blocks_hash", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)  # paragraph, heading, table, code
    page_from: Mapped[int] = mapped_column(Integer, nullable=False)
    page_to: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str] = mapped_column(String(512), nullable=False)
    source_anchor: Mapped[str | None] = mapped_column(String(256), nullable=True)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dedup_signature_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class SearchUnit(Base):
    __tablename__ = "search_units"
    __table_args__ = (
        Index("idx_search_units_version", "document_version_id"),
        Index("idx_search_units_security", "security_partition_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    block_from_id: Mapped[str] = mapped_column(ForeignKey("canonical_blocks.id"), nullable=False)
    block_to_id: Mapped[str] = mapped_column(ForeignKey("canonical_blocks.id"), nullable=False)
    security_partition_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_from: Mapped[int] = mapped_column(Integer, nullable=False)
    page_to: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class KnowledgeGraphEdge(Base):
    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (
        UniqueConstraint("source_unit_id", "target_unit_id", "edge_type", name="uq_knowledge_graph_edges_pair_type"),
        Index("idx_knowledge_graph_edges_project", "project_id"),
        Index("idx_knowledge_graph_edges_source", "source_unit_id"),
        Index("idx_knowledge_graph_edges_target", "target_unit_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_unit_id: Mapped[str] = mapped_column(ForeignKey("search_units.id", ondelete="CASCADE"), nullable=False)
    target_unit_id: Mapped[str] = mapped_column(ForeignKey("search_units.id", ondelete="CASCADE"), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    calibrated_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class ProjectSearchState(Base):
    __tablename__ = "project_search_state"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slot_a_tree_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slot_b_tree_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_routing_slot: Mapped[str] = mapped_column(String(16), default="SLOT_A", nullable=False)
    active_tree_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_tree_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_search_epoch: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    last_switched_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class TreeManifest(Base):
    __tablename__ = "tree_manifests"
    __table_args__ = (
        Index("idx_tree_manifests_project", "project_id", "status"),
    )

    tree_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)
    leaf_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_leaf_size: Mapped[int] = mapped_column(Integer, nullable=False)
    giant_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    routing_recall_at_k: Mapped[float] = mapped_column(Float, nullable=False)
    escape_win_rate: Mapped[float] = mapped_column(Float, nullable=False)
    acl_blackhole_rate: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="INACTIVE", nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class TreeSnapshotLease(Base):
    __tablename__ = "tree_snapshot_leases"
    __table_args__ = (
        Index("idx_tree_snapshot_leases_slot_expiry", "project_id", "routing_slot", "expires_at"),
    )

    request_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    routing_slot: Mapped[str] = mapped_column(String(16), nullable=False)
    tree_version: Mapped[str] = mapped_column(String(64), nullable=False)
    search_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class TreeRoutingProfile(Base):
    __tablename__ = "tree_routing_profiles"
    __table_args__ = (
        Index(
            "idx_tree_routing_profiles_version_scope",
            "project_id",
            "tree_version",
            "source_id",
            "document_version_id",
            "partition_id",
        ),
    )

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tree_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    partition_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    accessible_unit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sparse_json: Mapped[list] = mapped_column(JSON, nullable=False)
    entities_json: Mapped[list] = mapped_column(JSON, nullable=False)
    profile_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
