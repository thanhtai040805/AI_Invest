"""Private answer votes and explicitly shared, permission-checked projections."""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime, new_id


class AnswerFeedback(IDMixin, TimestampMixin, Base):
    __tablename__ = "answer_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "voter_subject", name="uq_answer_feedback_message_voter"),
        CheckConstraint("rating IN ('LIKE', 'DISLIKE')", name="ck_answer_feedback_rating"),
        CheckConstraint(
            "(rating = 'LIKE' AND reason_category IS NULL AND reason_text IS NULL) OR "
            "(rating = 'DISLIKE' AND reason_category IS NOT NULL AND reason_category IN "
            "('INACCURATE', 'OUTDATED', 'IRRELEVANT_CITATION', 'INCOMPLETE', 'OTHER') AND "
            "(reason_category != 'OTHER' OR reason_text IS NOT NULL))",
            name="ck_answer_feedback_reason",
        ),
        CheckConstraint(
            "reason_text IS NULL OR (length(trim(reason_text)) > 0 AND length(reason_text) <= 1000)",
            name="ck_answer_feedback_reason_text",
        ),
        CheckConstraint("length(trim(tenant_id)) > 0", name="ck_answer_feedback_tenant"),
        CheckConstraint("length(trim(project_id)) > 0", name="ck_answer_feedback_project"),
        CheckConstraint("length(trim(voter_subject)) > 0", name="ck_answer_feedback_voter"),
        Index("ix_answer_feedback_created_id", "created_at", "id"),
        Index("ix_answer_feedback_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_answer_feedback_scope_created", "tenant_id", "project_id", "created_at", "id"),
        Index("ix_answer_feedback_rating_created", "rating", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    voter_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    reason_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnswerShare(IDMixin, TimestampMixin, Base):
    __tablename__ = "answer_shares"
    __table_args__ = (
        CheckConstraint("length(trim(tenant_id)) > 0", name="ck_answer_share_tenant"),
        CheckConstraint("length(trim(project_id)) > 0", name="ck_answer_share_project"),
        CheckConstraint("length(trim(creator_subject)) > 0", name="ck_answer_share_creator"),
        CheckConstraint("message_id != question_id", name="ck_answer_share_distinct_messages"),
        Index(
            "uq_answer_share_live_creator_message",
            "creator_subject", "message_id", unique=True,
            postgresql_where=text("revoked_at IS NULL"), sqlite_where=text("revoked_at IS NULL"),
        ),
        Index("ix_answer_shares_scope_created", "tenant_id", "project_id", "created_at", "id"),
        Index("ix_answer_shares_question", "question_id"),
        Index("ix_answer_shares_message", "message_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: secrets.token_urlsafe(32))
    creator_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_content: Mapped[str] = mapped_column(Text, nullable=False)
    answer_content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"), nullable=False)
    source_ids: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
