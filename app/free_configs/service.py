"""Service layer for the free-configs feature.

Holds the refresh orchestration and the read path used while rendering a
subscription. The read path is cached so a busy panel does not hit the database
once per subscription fetch.
"""

import asyncio
from datetime import UTC, datetime as dt

from aiocache import cached

from app.db import GetDB
from app.free_configs import crud
from app.free_configs.fetcher import HealthResult, iter_checked_batches, iter_fetched_sources
from app.free_configs.parser import ParsedConfig, label_uri
from app.free_configs.settings import get_settings
from app.utils.logger import get_logger

logger = get_logger("free-configs")

# guards against two refreshes (scheduler + manual API trigger) overlapping
_refresh_lock = asyncio.Lock()

_last_refresh: dict = {"at": None, "stats": None, "running": False}


async def refresh_pool() -> dict:
    """Fetch every enabled source, health-check the results, and replace the pool.

    Returns a stats dict describing the run. Safe to call concurrently: the
    second caller waits for the first instead of doing duplicate work.
    """
    if _refresh_lock.locked():
        logger.info("refresh already running, waiting for it to finish")

    async with _refresh_lock:
        _last_refresh["running"] = True
        started = dt.now(UTC)
        try:
            async with GetDB() as db:
                sources = await crud.get_sources(db, enabled_only=True)
                if not sources:
                    logger.warning("no enabled free-config sources configured, nothing to refresh")
                    stats = {
                        "sources": 0,
                        "fetched": 0,
                        "unique": 0,
                        "candidates": 0,
                        "duration_seconds": 0.0,
                        "errors": [],
                    }
                    _last_refresh.update({"at": started, "stats": stats})
                    return stats

                source_specs = [(source.id, source.url, source.is_base64) for source in sources]

            # Dedupe across sources as results arrive, remembering which source
            # first supplied each URI. Each source's own list is dropped as soon
            # as it has been merged, so peak memory does not scale with the
            # number of sources.
            unique: dict[str, ParsedConfig] = {}
            origin: dict[str, int | None] = {}
            outcomes: list[tuple[int, int, str | None]] = []
            fetched_total = 0
            errors: list[dict] = []

            async for result in iter_fetched_sources(source_specs):
                fetched_total += len(result.configs)
                if not result.ok:
                    errors.append({"url": result.url, "error": result.error})
                if result.source_id is not None:
                    outcomes.append((result.source_id, len(result.configs), result.error))
                for config in result.configs:
                    if config.uri_hash not in unique:
                        unique[config.uri_hash] = config
                        origin[config.uri_hash] = result.source_id
                result.configs.clear()

            candidates = list(unique.values())
            unique.clear()
            settings = await get_settings()
            if settings.max_configs > 0:
                candidates = candidates[: settings.max_configs]

            logger.info(
                "free-configs: %d fetched, %d unique, health-checking %d",
                fetched_total,
                len(origin),
                len(candidates),
            )

            # Keep only the healthy ones: on a big pool the unhealthy majority
            # is both useless and the bulk of the memory.
            healthy_results: list[HealthResult] = []
            checked = 0
            async for batch in iter_checked_batches(candidates):
                checked += len(batch)
                healthy_results.extend(result for result in batch if result.is_healthy)
                logger.debug("free-configs: checked %d/%d, %d healthy", checked, len(candidates), len(healthy_results))
            candidates.clear()

            async with GetDB() as db:
                # The pool is not touched here. What a refresh found goes into
                # the candidate tray, and only the owner moves anything across -
                # see crud.store_candidates for why.
                healthy_count = await crud.store_candidates(db, healthy_results, origin)
                await crud.refresh_manual_configs(db)
                for source_id, count, error in outcomes:
                    await crud.record_fetch_outcome(db, source_id, count, error, commit=False)
                await db.commit()

            # manual entries may have been restored into the pool
            _clear_group_cache()
            await _clear_pool_cache()

            duration = (dt.now(UTC) - started).total_seconds()
            stats = {
                "sources": len(source_specs),
                "fetched": fetched_total,
                "unique": len(origin),
                "candidates": healthy_count,
                "duration_seconds": round(duration, 1),
                "errors": errors,
            }
            logger.info(
                "free-configs: refresh done in %.1fs - %d reachable of %d checked, waiting in the tray",
                duration,
                healthy_count,
                checked,
            )
            _last_refresh.update({"at": started, "stats": stats})
            return stats
        finally:
            _last_refresh["running"] = False


async def recheck_configs(uri_hashes: list[str]) -> dict:
    """Re-run the reachability check over configs already in the pool.

    Nothing else does this. A refresh only checks what it has just fetched, so
    once a config is in the pool its health is frozen at the moment it was
    promoted - and two things then make that reading wrong. A config whose
    address a profile changed had its result cleared, because the old one
    described a different server, and stayed unreachable for ever after; a hand-
    added config never had a result at all. Both are then filtered out of every
    subscription, which is how a group can be assigned eighty-three configs and
    deliver twelve.

    The other direction is just as stale and quieter: a server that died last
    week still reads as reachable, because nobody asked again.

    An empty list means the whole pool.
    """
    async with GetDB() as db:
        configs = await crud.get_configs_by_hashes(db, uri_hashes)
        if not configs:
            return {"checked": 0, "reachable": 0, "unreachable": 0}

        # Only distinct endpoints are probed; the result is fanned back out to
        # every config that shares one.
        results: list[HealthResult] = []
        async for batch in iter_checked_batches(configs, force=True):
            results.extend(batch)

        reachable, unreachable = await crud.record_health(db, results)

    await _clear_pool_cache()
    _clear_group_cache()
    return {"checked": len(configs), "reachable": reachable, "unreachable": unreachable}


def last_refresh_info() -> dict:
    return {
        "running": _last_refresh["running"],
        "last_refresh_at": _last_refresh["at"],
        "last_stats": _last_refresh["stats"],
    }


@cached(ttl=60)
async def _cached_pool() -> list[str]:
    """Healthy URIs, labelled, ready to append to a subscription."""
    async with GetDB() as db:
        settings = await get_settings()
        pairs = await crud.get_healthy_configs(db, limit=settings.max_per_subscription)
    prefix = settings.remark_prefix
    return [label_uri(uri, prefix, remark_override=remark) for uri, remark in pairs]


async def invalidate_pool_cache() -> None:
    """Public entry point for the API: an override, assignment or setting changed."""
    _clear_group_cache()
    await _clear_pool_cache()


async def _clear_pool_cache() -> None:
    try:
        await _cached_pool.cache.clear()
    except Exception as exc:  # noqa: BLE001 - cache backend problems must not break a refresh
        logger.debug("could not clear free-configs pool cache: %s", exc)


@cached(ttl=60)
async def _cached_group_mode_enabled() -> bool:
    """Whether any group at all is opted in (lets us skip the per-user query)."""
    async with GetDB() as db:
        return bool(await crud.get_enabled_group_ids(db))


async def invalidate_access_cache() -> None:
    """Drop the cached group-opt-in lookup after the access list changes."""
    _clear_group_cache()
    try:
        await _cached_group_mode_enabled.cache.clear()
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not clear free-configs access cache: %s", exc)


async def user_is_eligible(user_id: int) -> bool:
    """Decide whether this user should receive free configs."""
    settings = await get_settings()
    if not settings.enabled:
        return False
    if settings.mode == "all":
        return True
    if not await _cached_group_mode_enabled():
        return False
    async with GetDB() as db:
        return await crud.user_has_access(db, user_id)


# Per-group results, cached briefly and keyed by the user's set of groups.
# The whole-pool case keeps its own cache above; this one only exists because in
# group mode two users with different groups get different lists, so a single
# cached list would serve one of them the other's configs.
_group_cache: dict[tuple[int, ...], tuple[float, list[str]]] = {}
_GROUP_CACHE_TTL = 60


def _clear_group_cache() -> None:
    _group_cache.clear()


async def _configs_for_groups(group_ids: list[int]) -> list[str]:
    key = tuple(sorted(group_ids))
    now = asyncio.get_running_loop().time()
    cached = _group_cache.get(key)
    if cached and now - cached[0] < _GROUP_CACHE_TTL:
        return cached[1]

    settings = await get_settings()
    async with GetDB() as db:
        pairs = await crud.get_configs_for_groups(db, list(key), limit=settings.max_per_subscription)
    prefix = settings.remark_prefix
    uris = [label_uri(uri, prefix, remark_override=remark) for uri, remark in pairs]

    # keep the cache from growing without bound on a panel with many group
    # combinations - it is a short-lived optimisation, not a store
    if len(_group_cache) > 512:
        _group_cache.clear()
    _group_cache[key] = (now, uris)
    return uris


async def get_configs_for_user(user_id: int) -> list[str]:
    """Return the free config URIs this user should get (empty when not eligible)."""
    settings = await get_settings()
    if not settings.enabled:
        return []

    if settings.mode == "all":
        return await _cached_pool()

    if not await _cached_group_mode_enabled():
        return []

    async with GetDB() as db:
        group_ids = await crud.get_user_group_ids(db, user_id)
        if not group_ids:
            return []
        allowed = set(await crud.get_enabled_group_ids(db))

    eligible = [group_id for group_id in group_ids if group_id in allowed]
    if not eligible:
        return []
    return await _configs_for_groups(eligible)
