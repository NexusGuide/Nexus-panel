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
from app.free_configs.fetcher import HealthResult, check_configs, fetch_sources
from app.free_configs.parser import ParsedConfig, label_uri
from app.utils.logger import get_logger
from config import free_configs_settings

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
                        "healthy": 0,
                        "duration_seconds": 0.0,
                        "errors": [],
                    }
                    _last_refresh.update({"at": started, "stats": stats})
                    return stats

                source_specs = [(source.id, source.url, source.is_base64) for source in sources]

            results = await fetch_sources(source_specs)

            # dedupe across sources, remembering which source first supplied each URI
            unique: dict[str, ParsedConfig] = {}
            origin: dict[str, int | None] = {}
            fetched_total = 0
            errors: list[dict] = []

            for result in results:
                fetched_total += len(result.configs)
                if not result.ok:
                    errors.append({"url": result.url, "error": result.error})
                for config in result.configs:
                    if config.uri_hash not in unique:
                        unique[config.uri_hash] = config
                        origin[config.uri_hash] = result.source_id

            candidates = list(unique.values())
            if free_configs_settings.max_configs > 0:
                candidates = candidates[: free_configs_settings.max_configs]

            logger.info(
                "free-configs: %d fetched, %d unique, health-checking %d",
                fetched_total,
                len(unique),
                len(candidates),
            )

            health_results: list[HealthResult] = await check_configs(candidates)

            async with GetDB() as db:
                healthy_count = await crud.replace_configs(db, health_results, origin)
                for result in results:
                    if result.source_id is not None:
                        await crud.record_fetch_outcome(
                            db, result.source_id, len(result.configs), result.error, commit=False
                        )
                await db.commit()

            # the pool changed - drop the cached read path
            await _clear_pool_cache()

            duration = (dt.now(UTC) - started).total_seconds()
            stats = {
                "sources": len(source_specs),
                "fetched": fetched_total,
                "unique": len(unique),
                "healthy": healthy_count,
                "duration_seconds": round(duration, 1),
                "errors": errors,
            }
            logger.info(
                "free-configs: refresh done in %.1fs - %d healthy of %d checked",
                duration,
                healthy_count,
                len(candidates),
            )
            _last_refresh.update({"at": started, "stats": stats})
            return stats
        finally:
            _last_refresh["running"] = False


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
        uris = await crud.get_healthy_configs(db, limit=free_configs_settings.max_per_subscription)
    prefix = free_configs_settings.remark_prefix
    return [label_uri(uri, prefix) for uri in uris]


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
    try:
        await _cached_group_mode_enabled.cache.clear()
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not clear free-configs access cache: %s", exc)


async def user_is_eligible(user_id: int) -> bool:
    """Decide whether this user should receive free configs."""
    if not free_configs_settings.enabled:
        return False
    if free_configs_settings.mode == "all":
        return True
    if not await _cached_group_mode_enabled():
        return False
    async with GetDB() as db:
        return await crud.user_has_access(db, user_id)


async def get_configs_for_user(user_id: int) -> list[str]:
    """Return the free config URIs this user should get (empty when not eligible)."""
    if not await user_is_eligible(user_id):
        return []
    return await _cached_pool()
