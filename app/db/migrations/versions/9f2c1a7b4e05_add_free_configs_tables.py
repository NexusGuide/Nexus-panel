"""add free configs tables

Revision ID: 9f2c1a7b4e05
Revises: 7c4bd5128e62
Create Date: 2026-08-31 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
import app.db.compiles_types


# revision identifiers, used by Alembic.
revision = "9f2c1a7b4e05"
down_revision = "7c4bd5128e62"
branch_labels = None
depends_on = None


def _id_column_type():
    """Match the id type already in use by this database.

    Older installs may still be on INTEGER ids while newer ones are BIGINT; the
    foreign key to groups.id has to line up with whichever is present.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    groups_id_type = next(col["type"] for col in inspector.get_columns("groups") if col["name"] == "id")
    is_bigint = "BIGINT" in str(groups_id_type).upper()
    return app.db.compiles_types.SqliteCompatibleBigInteger() if is_bigint else sa.Integer()


def upgrade() -> None:
    col_type = _id_column_type()

    op.create_table(
        "free_config_sources",
        sa.Column("id", col_type, autoincrement=True, nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("remark", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("is_base64", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_fetch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fetch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_free_config_sources")),
        sa.UniqueConstraint("url", name=op.f("uq_free_config_sources_url")),
    )

    op.create_table(
        "free_configs",
        sa.Column("id", col_type, autoincrement=True, nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("uri_hash", sa.String(length=64), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("address", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_healthy", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_id", col_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["free_config_sources.id"],
            name=op.f("fk_free_configs_source_id_free_config_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_free_configs")),
        sa.UniqueConstraint("uri_hash", name=op.f("uq_free_configs_uri_hash")),
    )
    with op.batch_alter_table("free_configs", schema=None) as batch_op:
        batch_op.create_index("ix_free_configs_is_healthy", ["is_healthy"], unique=False)

    op.create_table(
        "free_config_group_access",
        sa.Column("group_id", col_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_free_config_group_access_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", name=op.f("pk_free_config_group_access")),
    )


def downgrade() -> None:
    op.drop_table("free_config_group_access")
    with op.batch_alter_table("free_configs", schema=None) as batch_op:
        batch_op.drop_index("ix_free_configs_is_healthy")
    op.drop_table("free_configs")
    op.drop_table("free_config_sources")
