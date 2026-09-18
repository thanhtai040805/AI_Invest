"""Remove the retired MOAT/competitive-evidence persistence layer.

Financial Quality is sourced from ai-invest and GIL is the only semantic
document graph layer retained by SAG.
"""

from alembic import op


revision = "202609140006"
down_revision = "202609130005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("competitive_evidence_signals")


def downgrade() -> None:
    # The retired table is intentionally not recreated. Restoring the old
    # MOAT contract would reintroduce a decision input that is no longer part
    # of the system architecture.
    pass
