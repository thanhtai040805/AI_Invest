"""Add the internal system identity used by the legacy v1 API."""

from alembic import op
import sqlalchemy as sa


revision = "202609140007"
down_revision = "202609140006"
branch_labels = None
depends_on = None

SCHEMA = "sag"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(120), nullable=False, server_default="System Local Agent"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True, schema=SCHEMA)
    op.execute(
        sa.text(
            "INSERT INTO sag.users (id, email, password_hash, name, is_active) "
            "VALUES ('dev_system_user', 'system@local.aiinvest', 'internal-system-identity', 'System Local Agent', true)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
