"""Periodic refresh of the free-configs pool.

Registered only when the feature is enabled and this process owns the scheduler,
so multi-worker deployments do not refresh N times in parallel.
"""

from datetime import UTC, datetime as dt, timedelta as td

from app import scheduler
from app.free_configs.service import refresh_pool
from app.utils.logger import get_logger
from config import free_configs_settings, runtime_settings

logger = get_logger("free-configs")


async def refresh_free_configs():
    try:
        await refresh_pool()
    except Exception as exc:  # noqa: BLE001 - a failed refresh must not kill the scheduler
        logger.error("free-configs refresh failed: %s", exc, exc_info=True)


if free_configs_settings.enabled and runtime_settings.role.runs_scheduler:
    scheduler.add_job(
        refresh_free_configs,
        "interval",
        seconds=free_configs_settings.refresh_interval,
        coalesce=True,
        max_instances=1,
        id="refresh_free_configs",
        replace_existing=True,
        # give the panel a moment to finish booting before hitting the network
        start_date=dt.now(UTC) + td(seconds=60),
    )
