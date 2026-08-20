from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class UniverseOverview(IDMixin, TimestampMixin, Base):
    """An atomically swappable aggregate overview snapshot."""

    __tablename__ = "universe_overviews"
    __table_args__ = (
        Index("ix_universe_overview_user_active", "user_id", "is_active"),
        Index("ix_universe_overview_user_status", "user_id", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="building", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    partition_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    relation_count: Mapped[int] = mapped_column(Integer, default=0)
    bounds: Mapped[dict] = mapped_column("bounds_json", JSON, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    as_of: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class UniversePartition(IDMixin, TimestampMixin, Base):
    """A bounded virtual partition; no event/entity rows are copied here."""

    __tablename__ = "universe_partitions"
    __table_args__ = (
        UniqueConstraint(
            "overview_id", "source_id", "kind", "key", name="uq_universe_partition_key"
        ),
        Index("ix_universe_partition_overview_kind", "overview_id", "kind"),
    )

    overview_id: Mapped[str] = mapped_column(
        ForeignKey("universe_overviews.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("universe_partitions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(160))
    label: Mapped[str] = mapped_column(String(200))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    z: Mapped[float] = mapped_column(Float, default=0.0)
    radius: Mapped[float] = mapped_column(Float, default=120.0)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    relation_count: Mapped[int] = mapped_column(Integer, default=0)
    density: Mapped[float] = mapped_column(Float, default=0.0)
    seed: Mapped[int] = mapped_column(Integer, default=0)
    time_range: Mapped[dict] = mapped_column("time_range_json", JSON, default=dict)
    time_buckets: Mapped[list] = mapped_column("time_buckets_json", JSON, default=list)
    importance: Mapped[float] = mapped_column(Float, default=0.0)


class UniverseDirtySource(IDMixin, TimestampMixin, Base):
    """Marks a source whose visualization projection needs rebuilding."""

    __tablename__ = "universe_dirty_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", name="uq_universe_dirty_user_source"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(64), default="changed")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ExplorationSession(IDMixin, TimestampMixin, Base):
    """Search-first exploration history, deliberately separate from chat threads."""

    __tablename__ = "exploration_sessions"
    __table_args__ = (Index("ix_exploration_user_updated", "user_id", "updated_at"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="Khám phá mới")
    source_ids: Mapped[list] = mapped_column("source_ids_json", JSON, default=list)


class ExplorationStep(IDMixin, TimestampMixin, Base):
    __tablename__ = "exploration_steps"
    __table_args__ = (Index("ix_exploration_step_session_created", "session_id", "created_at"),)

    session_id: Mapped[str] = mapped_column(
        ForeignKey("exploration_sessions.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    source_ids: Mapped[list] = mapped_column("source_ids_json", JSON, default=list)
    event_refs: Mapped[list] = mapped_column("event_refs_json", JSON, default=list)
    entity_refs: Mapped[list] = mapped_column("entity_refs_json", JSON, default=list)
    relation_refs: Mapped[list] = mapped_column("relation_refs_json", JSON, default=list)
    evidence_refs: Mapped[list] = mapped_column("evidence_refs_json", JSON, default=list)
    camera: Mapped[dict] = mapped_column("camera_json", JSON, default=dict)
