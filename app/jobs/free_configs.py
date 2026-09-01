"""Periodic refresh of the free-configs pool.

Registered only when the feature is enabled in the environment and this process
owns the scheduler, so multi-worker deployments do not refresh N times in
parallel.

The job ticks on a short fixed interval and decides for itself whether a refresh
is due. That indirection exists because the refresh interval is now editable
from the panel: a job scheduled with the interval read at boot would keep the
old cadence until someone restarted the container, which is a confusing way for
a settings page to behave.
"""

from datetime import UTC, datetime as dt, timedelta as td

from app import scheduler
from app.db import GetDB
from app.free_configs import crud
from app.free_configs.service import refresh_pool
from app.free_configs.settings import get_settings
from app.utils.logger import get_logger
from config import free_configs_settings, runtime_settings

logger = get_logger("free-configs")

# How often we wake up to ask "is a refresh due yet?" - not how often we refresh.
TICK_SECONDS = 300


async def refresh_free_configs():
    try:
        settings = await get_settings()
        if not settings.enabled:
            return

        # The pool's own timestamp is the reference, not an in-process variable:
        # it survives a restart, so restarting the panel does not trigger a full
        # fetch-and-check of ten thousand endpoints.
        async with GetDB() as db:
            last = (await crud.get_pool_stats(db))["last_checked_at"]

        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            elapsed = (dt.now(UTC) - last).total_seconds()
            if elapsed < settings.refresh_interval:
                return

        await refresh_pool()
    except Exception as exc:  # noqa: BLE001 - a failed refresh must not kill the scheduler
        logger.error("free-configs refresh failed: %s", exc, exc_info=True)


if free_configs_settings.enabled and runtime_settings.role.runs_scheduler:
    scheduler.add_job(
        refresh_free_configs,
        "interval",
        seconds=TICK_SECONDS,
        coalesce=True,
        max_instances=1,
        id="refresh_free_configs",
        replace_existing=True,
        # give the panel a moment to finish booting before hitting the network
        start_date=dt.now(UTC) + td(seconds=60),
    )
