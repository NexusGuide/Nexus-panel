"""add free configs serve_to

Which user states still receive free configs. Traffic through them cannot be
metered - it never touches this panel - so an expired or limited user who keeps
receiving them keeps working indefinitely. The default is therefore the
strictest option; a panel whose whole offering is free configs can loosen it.

Revision ID: 7a4c02d5e91b
Revises: 5e0b93af71c2
Create Date: 2026-09-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7a4c02d5e91b"
down_revision = "5e0b93af71c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("free_config_settings") as batch:
        batch.add_column(sa.Column("serve_to", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("free_config_settings") as batch:
        batch.drop_column("serve_to")
