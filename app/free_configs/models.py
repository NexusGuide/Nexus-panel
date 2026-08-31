"""Database models for the free-configs feature.

These are intentionally declared in their own module (instead of being appended
to ``app/db/models.py``) so that this fork stays cheap to rebase on upstream:
they register themselves on the shared ``Base.metadata`` at import time, exactly
like the upstream tables, without touching an upstream file.
"""

from datetime import UTC, datetime as dt

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.compiles_types import SqliteCompatibleBigInteger
from app.db.models import IdMixin


class FreeConfigSource(Base, IdMixin):
    """A public list of proxy URIs that gets polled periodically."""

    __tablename__ = "free_config_sources"

    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    remark: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # whole response body is base64 encoded and must be decoded before splitting
    is_base64: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="0")
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False, server_default="1")
    last_fetch_at: Mapped[dt | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True)
    last_fetch_count: Mapped[int] = mapped_column(default=0, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True)
    created_at: Mapped[dt] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: dt.now(UTC), init=False, nullable=False
    )


class FreeConfig(Base, IdMixin):
    """One proxy URI harvested from a source, plus its last health-check result."""

    __tablename__ = "free_configs"

    uri: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 of the uri - dedupes across sources and keeps the unique index narrow
    uri_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    port: Mapped[int] = mapped_column(default=0, nullable=False)
    is_healthy: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(default=None, nullable=True)
    last_checked_at: Mapped[dt | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True)
    source_id: Mapped[int | None] = mapped_column(
        SqliteCompatibleBigInteger,
        ForeignKey("free_config_sources.id", ondelete="SET NULL"),
        default=None,
        nullable=True,
    )
    created_at: Mapped[dt] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: dt.now(UTC), init=False, nullable=False
    )

    __table_args__ = (Index("ix_free_configs_is_healthy", "is_healthy"),)


class FreeConfigGroupAccess(Base):
    """Opt-in list of groups whose members receive free configs.

    Only consulted when ``FREE_CONFIGS_MODE=groups``.
    """

    __tablename__ = "free_config_group_access"

    group_id: Mapped[int] = mapped_column(
        SqliteCompatibleBigInteger,
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
