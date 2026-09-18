"""replace competitive moat schema with business quality evidence

Revision ID: 202609130005
Revises: 202609100004
"""
from alembic import op
import sqlalchemy as sa

revision = "202609130005"
down_revision = "202609100004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("moat_signals", "competitive_evidence_signals")
    op.alter_column(
        "competitive_evidence_signals",
        "pillar",
        new_column_name="evidence_type",
        existing_type=sa.String(length=64),
    )


def downgrade() -> None:
    op.alter_column(
        "competitive_evidence_signals",
        "evidence_type",
        new_column_name="pillar",
        existing_type=sa.String(length=64),
    )
    op.rename_table("competitive_evidence_signals", "moat_signals")
