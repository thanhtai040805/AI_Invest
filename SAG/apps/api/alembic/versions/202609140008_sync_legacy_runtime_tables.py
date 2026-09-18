"""Create legacy runtime tables still consumed by the SAG web shell."""

from alembic import op


revision = "202609140008"
down_revision = "202609140007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The v2 migrations intentionally create the financial tables explicitly.
    # The web shell still uses the ORM-backed settings/agent/source tables, so
    # create only the ORM tables that are absent in this database.  Existing
    # financial tables remain untouched by checkfirst=True.
    op.execute("SET search_path TO sag, public")
    from sag_api.db.base import Base
    from sag_api.db import models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Runtime tables are shared with the web shell; do not drop them in a
    # downgrade because doing so would destroy user/runtime data.
    pass
