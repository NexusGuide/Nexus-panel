"""add free configs admin control

Adds what the panel's Free Configs page needs:

* ``free_config_overrides`` - one row per admin decision about a config
  (switched off, renamed, or added by hand). Keyed by the config's content
  hash rather than its row id, because ``free_configs`` is emptied and rebuilt
  on every refresh; a decision stored on a pool row would not survive the day.
* ``free_config_settings`` - a single row of nullable columns. Null means "use
  the .env value", so an install that never opens the page behaves exactly as
  its environment says.
* ``free_configs.is_enabled`` / ``is_manual`` - the override mirrored onto the
  pool row, so the subscription query stays one indexed lookup instead of a
  join on every request.

Revision ID: 3d81ac6f2b19
Revises: 9f2c1a7b4e05
Create Date: 2026-09-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3d81ac6f2b19"
down_revision = "9f2c1a7b4e05"
branch_labels = None
depends_on = None


def _id_column_type():
    """Match the id type the free_configs table already uses.

    Same reasoning as the migration that created these tables: an old install
    may still be on INTEGER while a newer one is BIGINT.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for column in inspector.get_columns("free_configs"):
        if column["name"] == "id":
            return column["type"].__class__()
    return sa.BigInteger()


def upgrade() -> None:
    id_type = _id_column_type()

    op.create_table(
        "free_config_overrides",
        sa.Column("id", id_type, autoincrement=True, nullable=False),
        sa.Column("uri_hash", sa.String(length=64), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("remark", sa.String(length=256), nullable=True),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uri_hash"),
    )
    op.create_index("ix_free_config_overrides_is_enabled", "free_config_overrides", ["is_enabled"])

    op.create_table(
        "free_config_settings",
        sa.Column("id", id_type, autoincrement=True, nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column("refresh_interval", sa.Integer(), nullable=True),
        sa.Column("fetch_timeout", sa.Integer(), nullable=True),
        sa.Column("health_check", sa.Boolean(), nullable=True),
        sa.Column("tcp_timeout", sa.Integer(), nullable=True),
        sa.Column("max_concurrency", sa.Integer(), nullable=True),
        sa.Column("max_configs", sa.Integer(), nullable=True),
        sa.Column("max_per_endpoint", sa.Integer(), nullable=True),
        sa.Column("max_per_subscription", sa.Integer(), nullable=True),
        sa.Column("remark_prefix", sa.String(length=64), nullable=True),
        sa.Column("disabled", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("free_configs") as batch:
        batch.add_column(sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False))
        batch.add_column(sa.Column("is_manual", sa.Boolean(), server_default="0", nullable=False))

    # the subscription query filters on both flags together
    op.drop_index("ix_free_configs_is_healthy", table_name="free_configs")
    op.create_index("ix_free_configs_is_healthy", "free_configs", ["is_healthy", "is_enabled"])


def downgrade() -> None:
    op.drop_index("ix_free_configs_is_healthy", table_name="free_configs")
    op.create_index("ix_free_configs_is_healthy", "free_configs", ["is_healthy"])

    with op.batch_alter_table("free_configs") as batch:
        batch.drop_column("is_manual")
        batch.drop_column("is_enabled")

    op.drop_table("free_config_settings")
    op.drop_index("ix_free_config_overrides_is_enabled", table_name="free_config_overrides")
    op.drop_table("free_config_overrides")
