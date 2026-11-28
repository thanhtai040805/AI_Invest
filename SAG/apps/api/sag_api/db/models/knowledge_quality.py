"""Phase 9 versioned quality assessments, scored gaps and privacy-safe demand totals."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
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


class KnowledgeQueryDemandDaily(Base):
    __tablename__ = "knowledge_query_demand_daily"
    __table_args__ = (
        CheckConstraint("request_count >= 0", name="ck_knowledge_query_demand_nonnegative"),
        Index(
            "idx_knowledge_query_demand_scope_day",
            "tenant_id",
            "project_id",
            "security_partition_id",
            "tree_version",
            "day",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    security_partition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tree_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KnowledgeQualityAssessment(Base):
    __tablename__ = "knowledge_quality_assessments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "security_partition_id",
            "tree_version",
            "assessment_checksum",
            name="uq_knowledge_quality_assessment_identity",
        ),
        Index(
            "idx_knowledge_quality_assessment_scope",
            "tenant_id",
            "project_id",
            "security_partition_id",
            "tree_version",
            "as_of",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    security_partition_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tree_version: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    metric_definition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_formula_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    input_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason_codes_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class KnowledgeGap(Base):
    __tablename__ = "knowledge_gaps"
    __table_args__ = (
        UniqueConstraint("assessment_id", "node_id", name="uq_knowledge_gap_assessment_node"),
        Index("idx_knowledge_gaps_priority", "assessment_id", "eligible", "priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_quality_assessments.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance: Mapped[float | None] = mapped_column(Float, nullable=True)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason_codes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_refs_json: Mapped[list] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class KnowledgeGapQuestion(Base):
    __tablename__ = "knowledge_gap_questions"
    __table_args__ = (
        UniqueConstraint("gap_id", "checksum", name="uq_knowledge_gap_question_checksum"),
        Index("idx_knowledge_gap_questions_gap", "gap_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    gap_id: Mapped[str] = mapped_column(ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    reason_codes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
