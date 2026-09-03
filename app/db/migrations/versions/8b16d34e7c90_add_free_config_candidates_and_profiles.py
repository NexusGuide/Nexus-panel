"""add free config candidates and profiles

A refresh no longer replaces the pool. It writes what it found into
free_config_candidates and leaves free_configs alone, so the pool changes only
when the owner promotes something. free_config_profiles holds named sets of
field values that can be stamped onto configs in bulk.

Revision ID: 8b16d34e7c90
Revises: 7a4c02d5e91b
Create Date: 2026-09-03

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8b16d34e7c90"
down_revision = "7a4c02d5e91b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "free_config_candidates",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("uri_hash", sa.String(length=64), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("address", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_healthy", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("found_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["free_config_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uri_hash"),
    )
    op.create_index("ix_free_config_candidates_healthy", "free_config_candidates", ["is_healthy"])

    op.create_table(
        "free_config_profiles",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("fields", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("remark", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("free_config_profiles")
    op.drop_index("ix_free_config_candidates_healthy", table_name="free_config_candidates")
    op.drop_table("free_config_candidates")
