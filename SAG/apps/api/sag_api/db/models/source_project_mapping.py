from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin


class SourceProjectMapping(IDMixin, TimestampMixin, Base):
    """Audited assignment of one SAG Source to one external Project scope."""

    __tablename__ = "source_project_mappings"
    __table_args__ = (
        CheckConstraint(
            "state IN ('PENDING', 'CONFIRMED', 'REVOKED')",
            name="ck_source_project_mappings_state",
        ),
        CheckConstraint(
            "length(trim(organization_id)) > 0",
            name="ck_source_project_mappings_organization_id",
        ),
        CheckConstraint(
            "length(trim(project_id)) > 0",
            name="ck_source_project_mappings_project_id",
        ),
        CheckConstraint(
            "mapping_version > 0",
            name="ck_source_project_mappings_mapping_version",
        ),
        CheckConstraint(
            "state != 'CONFIRMED' OR (confirmed_at IS NOT NULL AND "
            "confirmed_by IS NOT NULL AND approval_ref IS NOT NULL)",
            name="ck_source_project_mappings_confirmation",
        ),
        CheckConstraint(
            "state != 'REVOKED' OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revocation_ref IS NOT NULL)",
            name="ck_source_project_mappings_revocation",
        ),
        Index(
            "uq_source_project_mappings_current_source",
            "source_id",
            unique=True,
            sqlite_where=text("state IN ('PENDING', 'CONFIRMED')"),
            postgresql_where=text("state IN ('PENDING', 'CONFIRMED')"),
        ),
        Index("ix_source_project_mappings_scope", "organization_id", "project_id", "state", "source_id"),
    )

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(String(256))
    project_id: Mapped[str] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    mapping_version: Mapped[int] = mapped_column(default=1, server_default="1")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    approval_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    revocation_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
