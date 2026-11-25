"""Dual-dialect SQLAlchemy ORM models for SAG-Native User Memory & Linked Sessions (Migration 0014).

Supports both PostgreSQL 16 (production) and SQLite (`sqlite+aiosqlite` in pytest) via
`JSON().with_variant(JSONB(), "postgresql")`. Strictly isolated by `(tenant_id, user_id)`
and completely separate from shared corporate `kg_nodes`/`kg_edges` and BCTC tables.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, UTCDateTime

_JSONB = JSON().with_variant(JSONB(), "postgresql")
_DOUBLE = Float().with_variant(DOUBLE_PRECISION(), "postgresql")

# Windows-safe infinity sentinel: 9999-12-31T23:59:59Z (NEVER call .timestamp() on year 9999 on Windows!)
INF_TS: int = 253402300799
_EPOCH_UTC: datetime = datetime(1970, 1, 1, tzinfo=UTC)
INF_DATETIME: datetime = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)


def _new_uuid_str() -> str:
    return str(uuid.uuid4())


def _inf_datetime() -> datetime:
    return INF_DATETIME


def safe_utc_timestamp(value: datetime | date | None, *, default_inf: bool = False) -> int:
    """Compute UTC epoch seconds using pure Python timedelta arithmetic (100% Windows-safe)."""
    if value is None:
        return INF_TS if default_inf else 0
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        dt_utc = dt.astimezone(UTC)
    else:
        dt_utc = datetime(value.year, value.month, value.day, 0, 0, 0, tzinfo=UTC)
    if dt_utc.year >= 9999:
        return INF_TS
    if dt_utc.year < 1970:
        return 0
    return int((dt_utc - _EPOCH_UTC).total_seconds())


def date_to_utc_ts(value: date) -> int:
    """Convert a calendar `date` to deterministic UTC midnight epoch seconds."""
    if value.year >= 9999:
        return INF_TS
    if value.year < 1970:
        return 0
    return int((datetime(value.year, value.month, value.day, tzinfo=UTC) - _EPOCH_UTC).total_seconds())


class UserMemoryEpisode(Base):
    """Per-user session episode record forming the backbone of Linked Sessions."""

    __tablename__ = "user_memory_episodes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "external_session_id",
            name="uq_user_memory_episodes_scope_session",
        ),
        Index(
            "idx_user_memory_episodes_scope_pit",
            "tenant_id",
            "user_id",
            "status",
            "is_backtest_sandbox",
            "source_as_of_date",
        ),
        Index("idx_user_memory_episodes_parent", "tenant_id", "user_id", "parent_session_id"),
    )

    episode_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    external_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_mode: Mapped[str] = mapped_column(
        String(16), default="GENERAL", server_default=text("'GENERAL'"), nullable=False
    )
    turn_range_start: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    turn_range_end: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    source_message_ids_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    tickers_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    sectors_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    macro_factors_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    topic_tags_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    cited_doc_version_ids_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    episode_summary: Mapped[str] = mapped_column(Text, default="", server_default=text("''"), nullable=False)
    staleness_status: Mapped[str] = mapped_column(
        String(24), default="FRESH", server_default=text("'FRESH'"), nullable=False
    )
    staleness_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_backtest_sandbox: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default=text("'ACTIVE'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserMemoryItem(Base):
    """Tri-Temporal Mem0-g user memory item with 3-state versioned Qdrant Outbox."""

    __tablename__ = "user_memory_items"
    __table_args__ = (
        Index(
            "idx_user_memory_items_active_pit",
            "tenant_id",
            "user_id",
            "is_backtest_sandbox",
            "pit_sensitivity",
            "valid_to",
            "tx_to",
            "source_as_of_date",
        ),
        Index(
            "idx_user_memory_items_slot",
            "tenant_id",
            "user_id",
            "canonical_slot_key",
            "valid_to",
            "tx_to",
        ),
        Index("idx_user_memory_items_hash", "tenant_id", "user_id", "content_hash"),
        Index("idx_user_memory_items_session", "tenant_id", "user_id", "origin_session_id"),
        Index("idx_user_memory_items_outbox", "qdrant_sync_status", "updated_at"),
    )

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    scope: Mapped[str] = mapped_column(
        String(24), default="USER_GLOBAL", server_default=text("'USER_GLOBAL'"), nullable=False
    )
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    pit_sensitivity: Mapped[str] = mapped_column(
        String(24), default="MARKET_BOUND", server_default=text("'MARKET_BOUND'"), nullable=False
    )
    canonical_slot_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload_json: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, default=dict, server_default=text("'{}'"), nullable=False
    )
    tickers_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    bound_doc_version_ids_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(_DOUBLE, default=0.85, server_default=text("0.85"), nullable=False)
    source_authority: Mapped[str] = mapped_column(
        String(32), default="USER_DECLARED", server_default=text("'USER_DECLARED'"), nullable=False
    )
    origin_episode_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("user_memory_episodes.episode_id", ondelete="SET NULL"), nullable=True
    )
    origin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    origin_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Tri-Temporal axes: PIT cutoff (`source_as_of_date`), Real-World Validity (`valid_*`), System Tx (`tx_*`)
    source_as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_backtest_sandbox: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(UTCDateTime(), default=_inf_datetime, nullable=False)
    tx_from: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    tx_to: Mapped[datetime] = mapped_column(UTCDateTime(), default=_inf_datetime, nullable=False)

    # Lineage, Staleness & Decay
    supersedes_memory_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("user_memory_items.memory_id", ondelete="SET NULL"), nullable=True
    )
    merged_from_ids_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    staleness_status: Mapped[str] = mapped_column(
        String(24), default="FRESH", server_default=text("'FRESH'"), nullable=False
    )
    staleness_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reinforcement_count: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    pinned_by_user: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    # 3-State Versioned Outbox for Qdrant (`PENDING_UPSERT | PENDING_PATCH | PENDING_DELETE | SYNCED | FAILED`)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qdrant_point_id: Mapped[str] = mapped_column(String(64), default=_new_uuid_str, nullable=False)
    qdrant_sync_status: Mapped[str] = mapped_column(
        String(24), default="PENDING_UPSERT", server_default=text("'PENDING_UPSERT'"), nullable=False
    )
    sync_version: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"), nullable=False)
    sync_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserMemoryEntity(Base):
    """Per-user personal graph node whose `canonical_key` aligns 1-1 with `kg_nodes.node_id` on read."""

    __tablename__ = "user_memory_entities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "canonical_key",
            name="uq_user_memory_entities_scope_key",
        ),
        Index("idx_user_memory_entities_scope_type", "tenant_id", "user_id", "entity_type"),
    )

    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases_json: Mapped[list[str]] = mapped_column(
        _JSONB, default=list, server_default=text("'[]'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserMemoryEdge(Base):
    """Tri-Temporal personal graph directed edge across the closed 16-relation ontology."""

    __tablename__ = "user_memory_edges"
    __table_args__ = (
        Index(
            "idx_user_memory_edges_src_active",
            "tenant_id",
            "user_id",
            "src_entity_id",
            "relation_type",
            "valid_to",
            "tx_to",
        ),
        Index(
            "idx_user_memory_edges_dst_active",
            "tenant_id",
            "user_id",
            "dst_entity_id",
            "relation_type",
            "valid_to",
            "tx_to",
        ),
        Index(
            "idx_user_memory_edges_conflict",
            "tenant_id",
            "user_id",
            "src_entity_id",
            "dst_entity_id",
            "conflict_group",
            "valid_to",
            "tx_to",
        ),
        Index("idx_user_memory_edges_session", "tenant_id", "user_id", "origin_session_id"),
    )

    edge_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    src_entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_memory_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    dst_entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_memory_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    conflict_group: Mapped[str | None] = mapped_column(String(24), nullable=True)
    weight: Mapped[float] = mapped_column(_DOUBLE, default=1.0, server_default=text("1.0"), nullable=False)
    fact_text: Mapped[str] = mapped_column(Text, default="", server_default=text("''"), nullable=False)
    memory_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("user_memory_items.memory_id", ondelete="SET NULL"), nullable=True
    )
    origin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_backtest_sandbox: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(UTCDateTime(), default=_inf_datetime, nullable=False)
    tx_from: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    tx_to: Mapped[datetime] = mapped_column(UTCDateTime(), default=_inf_datetime, nullable=False)
    superseded_by_edge_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("user_memory_edges.edge_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserMemoryJob(Base):
    """Dedicated durable job queue for `UserMemoryWorker` (decoupled from `InProcessAsyncQueue`)."""

    __tablename__ = "user_memory_jobs"
    __table_args__ = (
        Index("idx_user_memory_jobs_poll", "status", "available_at", "created_at"),
        Index("idx_user_memory_jobs_scope", "tenant_id", "user_id", "session_id"),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    job_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, default=dict, server_default=text("'{}'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", server_default=text("'QUEUED'"), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserMemoryAuditLog(Base):
    """Immutable audit log tracking every memory creation, reconciliation, edit, pin, and retraction."""

    __tablename__ = "user_memory_audit_log"
    __table_args__ = (
        Index("idx_user_memory_audit_scope_created", "tenant_id", "user_id", "created_at"),
        Index("idx_user_memory_audit_memory", "memory_id"),
    )

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(32), default="WORKER", server_default=text("'WORKER'"), nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage_json: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
