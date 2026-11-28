"""Persisted Project-scoped AI Chat Mind Map snapshots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime, new_id


class ProjectMindMapSnapshot(IDMixin, TimestampMixin, Base):
    __tablename__ = "project_mind_maps"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "partition_scope_hash",
            "topic_key",
            name="uq_project_mind_maps_scope_topic",
        ),
        CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="ck_project_mind_maps_tenant",
        ),
        CheckConstraint(
            "length(trim(project_id)) > 0",
            name="ck_project_mind_maps_project",
        ),
        CheckConstraint(
            "length(trim(partition_scope_hash)) > 0",
            name="ck_project_mind_maps_partition_hash",
        ),
        CheckConstraint(
            "length(topic_key) <= 200 AND ("
            "(topic_key = '' AND topic_display IS NULL) OR "
            "(length(trim(topic_key)) > 0 AND topic_display IS NOT NULL AND length(trim(topic_display)) > 0)"
            ")",
            name="ck_project_mind_maps_topic_pair",
        ),
        CheckConstraint(
            "length(trim(scope_fingerprint)) > 0",
            name="ck_project_mind_maps_fingerprint",
        ),
        CheckConstraint(
            "length(trim(generated_by_subject)) > 0",
            name="ck_project_mind_maps_generator",
        ),
        CheckConstraint(
            "(generation_mode = 'llm' AND fallback_reason IS NULL) OR "
            "(generation_mode = 'outline_fallback' AND fallback_reason IS NOT NULL "
            "AND fallback_reason IN ('LLM_UNAVAILABLE', 'LLM_INVALID_OUTPUT'))",
            name="ck_project_mind_maps_generation_mode",
        ),
        Index(
            "ix_project_mind_maps_project_updated",
            "tenant_id",
            "project_id",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    partition_scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    topic_key: Mapped[str] = mapped_column(
        String(200), default="", server_default=text("''"), nullable=False
    )
    topic_display: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scope_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    generation_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    tree_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    citations_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    used_document_version_ids: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    generated_by_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
