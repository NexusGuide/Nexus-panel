"""Database access for the free-configs feature."""

from datetime import UTC, datetime as dt

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Group, users_groups_association
from app.free_configs.fetcher import HealthResult
from app.free_configs.models import FreeConfig, FreeConfigGroupAccess, FreeConfigSource

# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #


async def get_sources(db: AsyncSession, enabled_only: bool = False) -> list[FreeConfigSource]:
    stmt = select(FreeConfigSource).order_by(FreeConfigSource.id)
    if enabled_only:
        stmt = stmt.where(FreeConfigSource.is_enabled.is_(True))
    return list((await db.execute(stmt)).scalars().all())


async def get_source(db: AsyncSession, source_id: int) -> FreeConfigSource | None:
    return (await db.execute(select(FreeConfigSource).where(FreeConfigSource.id == source_id))).scalar_one_or_none()


async def get_source_by_url(db: AsyncSession, url: str) -> FreeConfigSource | None:
    return (await db.execute(select(FreeConfigSource).where(FreeConfigSource.url == url))).scalar_one_or_none()


async def create_source(db: AsyncSession, url: str, remark: str = "", is_base64: bool = False) -> FreeConfigSource:
    source = FreeConfigSource(url=url, remark=remark, is_base64=is_base64)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def update_source(
    db: AsyncSession,
    source: FreeConfigSource,
    *,
    remark: str | None = None,
    is_base64: bool | None = None,
    is_enabled: bool | None = None,
) -> FreeConfigSource:
    if remark is not None:
        source.remark = remark
    if is_base64 is not None:
        source.is_base64 = is_base64
    if is_enabled is not None:
        source.is_enabled = is_enabled
    await db.commit()
    await db.refresh(source)
    return source


async def delete_source(db: AsyncSession, source: FreeConfigSource) -> None:
    await db.delete(source)
    await db.commit()


async def record_fetch_outcome(
    db: AsyncSession, source_id: int, count: int, error: str | None, commit: bool = True
) -> None:
    await db.execute(
        update(FreeConfigSource)
        .where(FreeConfigSource.id == source_id)
        .values(last_fetch_at=dt.now(UTC), last_fetch_count=count, last_error=error)
    )
    if commit:
        await db.commit()


# --------------------------------------------------------------------------- #
# configs
# --------------------------------------------------------------------------- #


INSERT_CHUNK_SIZE = 500


async def replace_configs(db: AsyncSession, results: list[HealthResult], source_ids: dict[str, int | None]) -> int:
    """Swap the pool for a freshly harvested one.

    The pool is a cache of somebody else's data, so a straight replace is both
    simpler and more correct than trying to merge: entries that vanished from
    every upstream source should stop being served.

    Only healthy configs are stored - an unhealthy entry is of no use to a
    subscription, and keeping tens of thousands of dead ones was both a memory
    and a disk cost for nothing.

    Rows go in as plain dicts through Core in chunks rather than as ORM
    instances: building one mapped object per config is what pushed a large
    refresh into an OOM kill.

    Returns the number of configs stored.
    """
    now = dt.now(UTC)
    rows = [
        {
            "uri": result.config.uri,
            "uri_hash": result.config.uri_hash,
            "protocol": result.config.protocol,
            "address": result.config.address,
            "port": result.config.port,
            "is_healthy": True,
            "latency_ms": result.latency_ms,
            "last_checked_at": now,
            "source_id": source_ids.get(result.config.uri_hash),
            "created_at": now,
        }
        for result in results
        if result.is_healthy
    ]

    await db.execute(delete(FreeConfig))
    for start in range(0, len(rows), INSERT_CHUNK_SIZE):
        await db.execute(insert(FreeConfig), rows[start : start + INSERT_CHUNK_SIZE])
    await db.commit()
    return len(rows)


async def get_healthy_configs(db: AsyncSession, limit: int = 0) -> list[str]:
    """Return healthy URIs, fastest first (unknown latency last)."""
    stmt = (
        select(FreeConfig.uri)
        .where(FreeConfig.is_healthy.is_(True))
        .order_by(FreeConfig.latency_ms.is_(None), FreeConfig.latency_ms.asc(), FreeConfig.id.asc())
    )
    if limit > 0:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_configs(db: AsyncSession, healthy_only: bool = True, limit: int = 100) -> list[FreeConfig]:
    """Inspect the pool, fastest endpoints first (unknown latency last)."""
    stmt = select(FreeConfig)
    if healthy_only:
        stmt = stmt.where(FreeConfig.is_healthy.is_(True))
    stmt = stmt.order_by(FreeConfig.latency_ms.is_(None), FreeConfig.latency_ms.asc(), FreeConfig.id.asc())
    stmt = stmt.limit(max(1, min(limit, 1000)))
    return list((await db.execute(stmt)).scalars().all())


async def get_pool_stats(db: AsyncSession) -> dict:
    total = (await db.execute(select(func.count()).select_from(FreeConfig))).scalar_one()
    healthy = (
        await db.execute(select(func.count()).select_from(FreeConfig).where(FreeConfig.is_healthy.is_(True)))
    ).scalar_one()
    last_checked = (await db.execute(select(func.max(FreeConfig.last_checked_at)))).scalar_one()
    return {"total": total, "healthy": healthy, "last_checked_at": last_checked}


# --------------------------------------------------------------------------- #
# group access
# --------------------------------------------------------------------------- #


async def get_enabled_group_ids(db: AsyncSession) -> list[int]:
    return list((await db.execute(select(FreeConfigGroupAccess.group_id))).scalars().all())


async def set_group_access(db: AsyncSession, group_ids: list[int]) -> list[int]:
    """Replace the opted-in group list. Unknown group ids are ignored."""
    await db.execute(delete(FreeConfigGroupAccess))
    if group_ids:
        valid = list((await db.execute(select(Group.id).where(Group.id.in_(group_ids)))).scalars().all())
        db.add_all([FreeConfigGroupAccess(group_id=group_id) for group_id in valid])
        await db.commit()
        return valid
    await db.commit()
    return []


async def user_has_access(db: AsyncSession, user_id: int) -> bool:
    """True when the user belongs to at least one opted-in group.

    Single indexed EXISTS query - this runs on the subscription hot path.
    """
    stmt = select(
        exists().where(
            users_groups_association.c.user_id == user_id,
            users_groups_association.c.groups_id.in_(select(FreeConfigGroupAccess.group_id)),
        )
    )
    return bool((await db.execute(stmt)).scalar())
