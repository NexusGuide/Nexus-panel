"""profiles carry a protocol

A profile stamps field values onto configs, and protocols disagree about what
those fields are called: in a vmess body the transport is "net" and "type" means
the header type, while a vless URI spells the transport "type". A profile
written for one protocol cannot be applied to another without corrupting it, so
a profile now says which protocol it is for.

Profiles made before this column existed get an empty protocol. The panel shows
them as needing a protocol chosen rather than guessing one - guessing wrong
would rewrite configs into something that no longer connects.

Revision ID: c3f81a70de24
Revises: 8b16d34e7c90
Create Date: 2026-09-03

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3f81a70de24"
down_revision = "8b16d34e7c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "free_config_profiles",
        sa.Column("protocol", sa.String(length=32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("free_config_profiles", "protocol")
