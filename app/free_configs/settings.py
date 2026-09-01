"""Effective settings: the .env defaults with the admin's edits laid over them.

The feature started out configured purely from the environment, which is right
for a master switch but wrong for everything an operator wants to tune while
watching the pool. So the environment now supplies defaults and one database row
supplies overrides; a column left null means "whatever .env says".

Two rules keep this honest:

* ``FREE_CONFIGS_ENABLED`` stays environment-only. The panel can switch the
  feature off, never on, so an install that never opted in cannot be turned on
  from a web form.
* Reads are cached briefly. The subscription path asks for settings on every
  request and must not pay for a query each time.
"""

import asyncio
from dataclasses import dataclass, fields as dataclass_fields
from datetime import UTC, datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.free_configs.models import FreeConfigSetting
from app.utils.logger import get_logger
from config import free_configs_settings as env_settings

logger = get_logger("free-configs")

# Columns an admin may change. Anything outside this list is environment-only.
EDITABLE = (
    "mode",
    "refresh_interval",
    "fetch_timeout",
    "health_check",
    "tcp_timeout",
    "max_concurrency",
    "max_configs",
    "max_per_endpoint",
    "max_per_subscription",
    "remark_prefix",
    "serve_to",
)

_CACHE_TTL_SECONDS = 10


@dataclass(frozen=True)
class EffectiveSettings:
    enabled: bool
    mode: str
    refresh_interval: int
    fetch_timeout: int
    health_check: bool
    tcp_timeout: float
    max_concurrency: int
    max_configs: int
    max_per_endpoint: int
    max_per_subscription: int
    remark_prefix: str
    serve_to: str


def _from_env() -> EffectiveSettings:
    return EffectiveSettings(
        enabled=env_settings.enabled,
        mode=env_settings.mode,
        refresh_interval=env_settings.refresh_interval,
        fetch_timeout=env_settings.fetch_timeout,
        health_check=env_settings.health_check,
        tcp_timeout=env_settings.tcp_timeout,
        max_concurrency=env_settings.max_concurrency,
        max_configs=env_settings.max_configs,
        max_per_endpoint=env_settings.max_per_endpoint,
        max_per_subscription=env_settings.max_per_subscription,
        remark_prefix=env_settings.remark_prefix,
        serve_to=env_settings.serve_to,
    )


_cached: EffectiveSettings | None = None
_cached_at: float = 0.0
_lock = asyncio.Lock()


def invalidate() -> None:
    """Drop the cache so the next read sees a just-saved change."""
    global _cached, _cached_at
    _cached = None
    _cached_at = 0.0


async def _load_row(db: AsyncSession) -> FreeConfigSetting | None:
    return (await db.execute(select(FreeConfigSetting).limit(1))).scalar_one_or_none()


def _merge(row: FreeConfigSetting | None) -> EffectiveSettings:
    base = _from_env()
    if row is None:
        return base
    values = {field.name: getattr(base, field.name) for field in dataclass_fields(EffectiveSettings)}
    for name in EDITABLE:
        override = getattr(row, name, None)
        if override is not None:
            values[name] = override
    # the row may switch the feature off, but never on
    values["enabled"] = base.enabled and not row.disabled
    return EffectiveSettings(**values)


async def get_settings() -> EffectiveSettings:
    """Environment defaults with the stored overrides applied, cached briefly."""
    global _cached, _cached_at

    loop = asyncio.get_running_loop()
    now = loop.time()
    if _cached is not None and now - _cached_at < _CACHE_TTL_SECONDS:
        return _cached

    async with _lock:
        # another caller may have refreshed it while we waited
        now = loop.time()
        if _cached is not None and now - _cached_at < _CACHE_TTL_SECONDS:
            return _cached
        try:
            from app.db import GetDB

            async with GetDB() as db:
                merged = _merge(await _load_row(db))
        except Exception as exc:  # noqa: BLE001
            # Before the migration runs the table does not exist yet, and a
            # database hiccup must not take the subscription path down with it.
            logger.debug("could not read free-configs settings, using .env: %s", exc)
            merged = _from_env()

        _cached = merged
        _cached_at = loop.time()
        return merged


async def read_row(db: AsyncSession) -> dict:
    """The stored overrides as a plain dict, for the API to show alongside defaults."""
    row = await _load_row(db)
    stored = {name: (getattr(row, name, None) if row else None) for name in EDITABLE}
    return {"stored": stored, "defaults": _from_env().__dict__, "disabled": bool(row.disabled) if row else False}


async def update_settings(db: AsyncSession, changes: dict) -> EffectiveSettings:
    """Persist overrides. A key set to None goes back to its .env default."""
    row = await _load_row(db)
    if row is None:
        row = FreeConfigSetting()
        db.add(row)

    for name, value in changes.items():
        if name == "disabled":
            row.disabled = bool(value)
        elif name in EDITABLE:
            setattr(row, name, value)
        else:
            raise ValueError(f"{name} is configured in .env only")

    row.updated_at = dt.now(UTC)
    await db.commit()
    invalidate()
    return await get_settings()
