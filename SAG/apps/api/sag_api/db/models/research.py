"""Phase 10 & Checkpoint D bounded research tasks persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
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


class ResearchTask(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        UniqueConstraint("gap_id", "checksum", name="uq_research_task_gap_checksum"),
        Index(
            "idx_research_tasks_scope_status",
            "tenant_id",
            "project_id",
            "security_partition_id",
            "tree_version",
            "status",
        ),
        Index("idx_research_tasks_gap", "gap_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    security_partition_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tree_version: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    gap_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_gap_questions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    reason_codes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    policy_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    budget_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    execution_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    target_urls_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fetch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingestion_run_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    document_version_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )
