"""Database access for the free-configs feature."""

from datetime import UTC, datetime as dt

from sqlalchemy import delete, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Group, users_groups_association
from app.free_configs.fetcher import HealthResult
from app.free_configs.models import FreeConfig, FreeConfigGroupAccess, FreeConfigOverride, FreeConfigSource
from app.free_configs.settings import get_settings

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
    overrides = await get_overrides_map(db)
    manual_hashes = {uri_hash for uri_hash, row in overrides.items() if row.uri is not None}

    # One server often carries dozens of near-identical configs. Serving all of
    # them pads a subscription with entries that all fail together when that
    # server goes down, so keep only a few per server - different credentials or
    # transports on the same host are worth a couple of tries, not thirty.
    #
    # "Same server" is (address, port, sni), not (address, port). Behind a CDN
    # the address is shared: an entire curated list can sit on one Cloudflare IP,
    # and keying on the address alone threw away all but three of them. What
    # actually distinguishes those proxies is the TLS server name.
    per_endpoint = (await get_settings()).max_per_endpoint
    if per_endpoint > 0:
        kept: list[HealthResult] = []
        seen: dict[tuple[str, int, str], int] = {}
        for result in results:
            # a hand-added config is never squeezed out by the cap
            if result.config.uri_hash in manual_hashes:
                kept.append(result)
                continue
            if not result.is_healthy:
                continue
            key = result.config.endpoint_key
            if seen.get(key, 0) >= per_endpoint:
                continue
            seen[key] = seen.get(key, 0) + 1
            kept.append(result)
        results = kept

    rows = [
        {
            "uri": result.config.uri,
            "uri_hash": result.config.uri_hash,
            "protocol": result.config.protocol,
            "address": result.config.address,
            "port": result.config.port,
            "is_healthy": result.is_healthy,
            # the admin's switch, re-applied to the freshly harvested pool
            "is_enabled": overrides[result.config.uri_hash].is_enabled
            if result.config.uri_hash in overrides
            else True,
            "is_manual": result.config.uri_hash in manual_hashes,
            "latency_ms": result.latency_ms,
            "last_checked_at": now,
            "source_id": source_ids.get(result.config.uri_hash),
            "created_at": now,
        }
        for result in results
        if result.is_healthy or result.config.uri_hash in manual_hashes
    ]

    await db.execute(delete(FreeConfig))
    for start in range(0, len(rows), INSERT_CHUNK_SIZE):
        await db.execute(insert(FreeConfig), rows[start : start + INSERT_CHUNK_SIZE])
    await db.commit()
    return len(rows)


async def get_healthy_configs(db: AsyncSession, limit: int = 0) -> list[tuple[str, str | None]]:
    """URIs to serve, fastest first, as ``(uri, remark_override)`` pairs.

    Excludes anything the admin switched off. Manual entries are served even
    when the health check could not reach them - they were added deliberately,
    and second-guessing that is not this function's job.
    """
    stmt = (
        select(FreeConfig.uri, FreeConfigOverride.remark)
        .outerjoin(FreeConfigOverride, FreeConfigOverride.uri_hash == FreeConfig.uri_hash)
        .where(FreeConfig.is_enabled.is_(True))
        .where(or_(FreeConfig.is_healthy.is_(True), FreeConfig.is_manual.is_(True)))
        .order_by(
            FreeConfig.is_manual.desc(),
            FreeConfig.latency_ms.is_(None),
            FreeConfig.latency_ms.asc(),
            FreeConfig.id.asc(),
        )
    )
    if limit > 0:
        stmt = stmt.limit(limit)
    return [(uri, remark) for uri, remark in (await db.execute(stmt)).all()]


async def get_configs_page(
    db: AsyncSession,
    *,
    search: str | None = None,
    protocol: str | None = None,
    status: str = "all",
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[tuple[FreeConfig, FreeConfigOverride | None]], int]:
    """One page of the pool for the admin UI, with each config's override.

    ``status`` is one of all / enabled / disabled / manual / unhealthy.
    """
    stmt = select(FreeConfig, FreeConfigOverride).outerjoin(
        FreeConfigOverride, FreeConfigOverride.uri_hash == FreeConfig.uri_hash
    )
    count_stmt = select(func.count()).select_from(FreeConfig)

    conditions = []
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(or_(FreeConfig.address.ilike(pattern), FreeConfig.uri.ilike(pattern)))
    if protocol:
        conditions.append(FreeConfig.protocol == protocol)
    if status == "enabled":
        conditions.append(FreeConfig.is_enabled.is_(True))
    elif status == "disabled":
        conditions.append(FreeConfig.is_enabled.is_(False))
    elif status == "manual":
        conditions.append(FreeConfig.is_manual.is_(True))
    elif status == "unhealthy":
        conditions.append(FreeConfig.is_healthy.is_(False))

    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = stmt.order_by(
        FreeConfig.is_manual.desc(),
        FreeConfig.latency_ms.is_(None),
        FreeConfig.latency_ms.asc(),
        FreeConfig.id.asc(),
    ).offset(max(0, offset)).limit(max(1, min(limit, 500)))

    rows = [(config, override) for config, override in (await db.execute(stmt)).all()]
    total = (await db.execute(count_stmt)).scalar_one()
    return rows, total


async def get_protocols(db: AsyncSession) -> list[str]:
    """Distinct protocols present in the pool, for the UI's filter."""
    stmt = select(FreeConfig.protocol).distinct().order_by(FreeConfig.protocol)
    return [p for p in (await db.execute(stmt)).scalars().all() if p]


async def get_pool_stats(db: AsyncSession) -> dict:
    total = (await db.execute(select(func.count()).select_from(FreeConfig))).scalar_one()
    healthy = (
        await db.execute(select(func.count()).select_from(FreeConfig).where(FreeConfig.is_healthy.is_(True)))
    ).scalar_one()
    disabled = (
        await db.execute(select(func.count()).select_from(FreeConfig).where(FreeConfig.is_enabled.is_(False)))
    ).scalar_one()
    manual = (
        await db.execute(select(func.count()).select_from(FreeConfig).where(FreeConfig.is_manual.is_(True)))
    ).scalar_one()
    last_checked = (await db.execute(select(func.max(FreeConfig.last_checked_at)))).scalar_one()
    return {
        "total": total,
        "healthy": healthy,
        "disabled": disabled,
        "manual": manual,
        "last_checked_at": last_checked,
    }


# --------------------------------------------------------------------------- #
# per-config overrides
# --------------------------------------------------------------------------- #


async def get_overrides_map(db: AsyncSession) -> dict[str, FreeConfigOverride]:
    rows = (await db.execute(select(FreeConfigOverride))).scalars().all()
    return {row.uri_hash: row for row in rows}


async def get_manual_overrides(db: AsyncSession) -> list[FreeConfigOverride]:
    stmt = select(FreeConfigOverride).where(FreeConfigOverride.uri.is_not(None))
    return list((await db.execute(stmt)).scalars().all())


async def get_config_by_hash(db: AsyncSession, uri_hash: str) -> FreeConfig | None:
    return (await db.execute(select(FreeConfig).where(FreeConfig.uri_hash == uri_hash))).scalar_one_or_none()


async def set_overrides_bulk(db: AsyncSession, uri_hashes: list[str], is_enabled: bool) -> int:
    """Switch many configs at once, in one pass rather than a query per config."""
    wanted = [h for h in dict.fromkeys(uri_hashes) if h]
    if not wanted:
        return 0

    existing = {
        row.uri_hash: row
        for row in (
            await db.execute(select(FreeConfigOverride).where(FreeConfigOverride.uri_hash.in_(wanted)))
        ).scalars().all()
    }
    for uri_hash in wanted:
        if uri_hash in existing:
            existing[uri_hash].is_enabled = is_enabled
        else:
            db.add(FreeConfigOverride(uri_hash=uri_hash, is_enabled=is_enabled))

    await db.execute(update(FreeConfig).where(FreeConfig.uri_hash.in_(wanted)).values(is_enabled=is_enabled))
    await db.commit()
    return len(wanted)


async def get_override(db: AsyncSession, uri_hash: str) -> FreeConfigOverride | None:
    stmt = select(FreeConfigOverride).where(FreeConfigOverride.uri_hash == uri_hash)
    return (await db.execute(stmt)).scalar_one_or_none()


async def set_override(
    db: AsyncSession,
    uri_hash: str,
    *,
    is_enabled: bool | None = None,
    remark: str | None = None,
    note: str | None = None,
    uri: str | None = None,
) -> FreeConfigOverride:
    """Create or update one config's override, and mirror it onto the pool row.

    The mirror is what lets the subscription path stay a single indexed query
    instead of joining overrides on every request.
    """
    override = await get_override(db, uri_hash)
    if override is None:
        override = FreeConfigOverride(uri_hash=uri_hash)
        db.add(override)

    if is_enabled is not None:
        override.is_enabled = is_enabled
    if remark is not None:
        override.remark = remark or None
    if note is not None:
        override.note = note or None
    if uri is not None:
        override.uri = uri

    if is_enabled is not None:
        await db.execute(update(FreeConfig).where(FreeConfig.uri_hash == uri_hash).values(is_enabled=is_enabled))

    await db.commit()
    await db.refresh(override)
    return override


async def clear_override(db: AsyncSession, uri_hash: str) -> bool:
    """Forget an override entirely. Manual entries also leave the pool."""
    override = await get_override(db, uri_hash)
    if override is None:
        return False
    was_manual = override.uri is not None
    await db.delete(override)
    if was_manual:
        await db.execute(delete(FreeConfig).where(FreeConfig.uri_hash == uri_hash))
    else:
        await db.execute(update(FreeConfig).where(FreeConfig.uri_hash == uri_hash).values(is_enabled=True))
    await db.commit()
    return True


async def upsert_manual_config(db: AsyncSession, config, remark: str | None = None) -> FreeConfig:
    """Add a hand-entered config to both the override table and the pool.

    It goes into the pool immediately rather than waiting for the next refresh,
    because an admin who just pasted a config expects to see it.
    """
    await set_override(db, config.uri_hash, uri=config.uri, remark=remark, is_enabled=True)

    now = dt.now(UTC)
    existing = (
        await db.execute(select(FreeConfig).where(FreeConfig.uri_hash == config.uri_hash))
    ).scalar_one_or_none()
    if existing is not None:
        existing.is_manual = True
        existing.is_enabled = True
        await db.commit()
        await db.refresh(existing)
        return existing

    row = FreeConfig(
        uri=config.uri,
        uri_hash=config.uri_hash,
        protocol=config.protocol,
        address=config.address,
        port=config.port,
        is_healthy=False,
        is_enabled=True,
        is_manual=True,
        latency_ms=None,
        last_checked_at=None,
        source_id=None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


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
