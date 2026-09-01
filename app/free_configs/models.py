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
    # the admin's on/off switch, copied here from free_config_overrides at
    # refresh time so the subscription read path stays a single indexed query
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False, server_default="1")
    # a config the admin added by hand: kept across refreshes, never dropped by
    # the per-endpoint cap, and not attributed to any source
    is_manual: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="0")
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

    # the subscription query filters on both flags at once
    __table_args__ = (Index("ix_free_configs_is_healthy", "is_healthy", "is_enabled"),)


class FreeConfigOverride(Base, IdMixin):
    """An admin's decision about one config, kept apart from the pool itself.

    The pool is replaced wholesale on every refresh, so anything stored on a
    ``free_configs`` row - "don't serve this one", "call it something else" -
    would be erased within a day. Overrides live here instead, keyed by the
    same content hash, and are re-applied to whatever the next refresh brings
    back. A hash that stops appearing upstream simply stops matching; the row
    costs nothing and starts working again if the config reappears.

    A manual entry is the same idea in reverse: ``uri`` is set, no source
    supplies it, and it is merged into the pool on every refresh.
    """

    __tablename__ = "free_config_overrides"

    uri_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False, server_default="1")
    # replaces the config's own name in the subscription when set
    remark: Mapped[str | None] = mapped_column(String(256), default=None, nullable=True)
    # set only for manually added configs; harvested ones leave it null
    uri: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True)
    created_at: Mapped[dt] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: dt.now(UTC), init=False, nullable=False
    )

    __table_args__ = (Index("ix_free_config_overrides_is_enabled", "is_enabled"),)


class FreeConfigSetting(Base, IdMixin):
    """The single row holding settings the admin can change from the panel.

    Every column is nullable and means "not set - use the value from .env".
    That keeps the environment as the source of defaults, so an untouched
    install behaves exactly as its .env says, while anything edited in the UI
    takes effect without a restart.
    """

    __tablename__ = "free_config_settings"

    mode: Mapped[str | None] = mapped_column(String(16), default=None, nullable=True)
    refresh_interval: Mapped[int | None] = mapped_column(default=None, nullable=True)
    fetch_timeout: Mapped[int | None] = mapped_column(default=None, nullable=True)
    health_check: Mapped[bool | None] = mapped_column(default=None, nullable=True)
    tcp_timeout: Mapped[int | None] = mapped_column(default=None, nullable=True)
    max_concurrency: Mapped[int | None] = mapped_column(default=None, nullable=True)
    max_configs: Mapped[int | None] = mapped_column(default=None, nullable=True)
    max_per_endpoint: Mapped[int | None] = mapped_column(default=None, nullable=True)
    max_per_subscription: Mapped[int | None] = mapped_column(default=None, nullable=True)
    remark_prefix: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True)
    # can only switch the feature off; FREE_CONFIGS_ENABLED in .env still has to
    # be true for any of this to run, so a fork install stays off by default
    disabled: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="0")
    updated_at: Mapped[dt] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: dt.now(UTC), init=False, nullable=False
    )


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
