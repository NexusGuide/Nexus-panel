"""add free config group configs

Lets a group be given specific free configs, the way it is given specific
inbounds. Keyed by the config's content hash rather than its row id, because the
pool is replaced wholesale on every refresh.

An opted-in group with no rows here keeps the previous behaviour and receives
the whole pool, so nothing changes for an install that has already been using
FREE_CONFIGS_MODE=groups.

Revision ID: 5e0b93af71c2
Revises: 3d81ac6f2b19
Create Date: 2026-09-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "5e0b93af71c2"
down_revision = "3d81ac6f2b19"
branch_labels = None
depends_on = None


def _group_id_type():
    """Match groups.id, which may be INTEGER on older installs and BIGINT on newer."""
    bind = op.get_bind()
    for column in sa.inspect(bind).get_columns("groups"):
        if column["name"] == "id":
            return column["type"].__class__()
    return sa.BigInteger()


def upgrade() -> None:
    op.create_table(
        "free_config_group_configs",
        sa.Column("group_id", _group_id_type(), nullable=False),
        sa.Column("uri_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "uri_hash"),
    )
    op.create_index("ix_free_config_group_configs_hash", "free_config_group_configs", ["uri_hash"])


def downgrade() -> None:
    op.drop_index("ix_free_config_group_configs_hash", table_name="free_config_group_configs")
    op.drop_table("free_config_group_configs")
