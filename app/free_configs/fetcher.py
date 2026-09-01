"""Network side of the free-configs feature: fetch source lists, health-check URIs.

Everything here is async and bounded by a semaphore so a large pool cannot
starve the event loop the panel serves requests on.
"""

import asyncio
from dataclasses import dataclass

import aiohttp

from app.free_configs.parser import ParsedConfig, decode_body, parse_many
from app.free_configs.settings import get_settings
from app.utils.logger import get_logger

logger = get_logger("free-configs")

USER_AGENT = "PasarGuard-FreeConfigs/1.0"


@dataclass
class FetchResult:
    source_id: int | None
    url: str
    configs: list[ParsedConfig]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class HealthResult:
    config: ParsedConfig
    is_healthy: bool
    latency_ms: int | None = None


async def fetch_source(
    session: aiohttp.ClientSession, url: str, is_base64: bool, source_id: int | None = None
) -> FetchResult:
    """Download one source list and parse it into configs."""
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as response:
            response.raise_for_status()
            body = await response.text()
    except asyncio.TimeoutError:
        return FetchResult(source_id=source_id, url=url, configs=[], error="timeout while fetching source")
    except aiohttp.ClientError as exc:
        return FetchResult(source_id=source_id, url=url, configs=[], error=f"{type(exc).__name__}: {exc}"[:512])
    except Exception as exc:  # noqa: BLE001 - a bad source must never break the whole refresh
        return FetchResult(source_id=source_id, url=url, configs=[], error=f"{type(exc).__name__}: {exc}"[:512])

    configs = parse_many(decode_body(body, is_base64))
    logger.debug("fetched %s -> %d parsable configs", url, len(configs))
    return FetchResult(source_id=source_id, url=url, configs=configs)


async def iter_fetched_sources(sources: list[tuple[int | None, str, bool]]):
    """Fetch every source concurrently, yielding each result as it arrives.

    Yielding (instead of returning one big list) lets the caller merge a
    source's configs into its dedup map and drop the per-source list right
    away, which keeps peak memory down on large source lists.

    ``sources`` is a list of ``(source_id, url, is_base64)`` tuples.
    """
    settings = await get_settings()
    timeout = aiohttp.ClientTimeout(total=settings.fetch_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            asyncio.create_task(fetch_source(session, url, is_base64, source_id))
            for source_id, url, is_base64 in sources
        ]
        try:
            for coro in asyncio.as_completed(tasks):
                yield await coro
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()


async def check_endpoint(address: str, port: int, semaphore: asyncio.Semaphore) -> int | None:
    """TCP-connect to one endpoint, returning the latency in ms or None.

    A successful connect only proves the port answers - it is not proof that the
    proxy protocol itself works. That caveat is surfaced in the API and docs.
    """
    settings = await get_settings()
    async with semaphore:
        loop = asyncio.get_running_loop()
        started = loop.time()
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(address, port),
                timeout=settings.tcp_timeout,
            )
            return int((loop.time() - started) * 1000)
        except (asyncio.TimeoutError, OSError, ValueError):
            return None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, asyncio.TimeoutError):
                    pass


async def check_config(config: ParsedConfig, semaphore: asyncio.Semaphore) -> HealthResult:
    """Health-check a single config (kept for tests and one-off checks)."""
    latency = await check_endpoint(config.address, config.port, semaphore)
    return HealthResult(config=config, is_healthy=latency is not None, latency_ms=latency)


async def iter_checked_batches(configs: list[ParsedConfig], batch_size: int = 500):
    """Health-check configs in bounded batches, yielding each batch's results.

    One giant ``asyncio.gather`` over every config allocates a coroutine, a task
    and a result object per entry up front - with tens of thousands of configs
    that is enough to get the process OOM-killed. Batching keeps the number of
    live objects flat regardless of pool size.
    """
    settings = await get_settings()
    if not settings.health_check:
        # health checking disabled: keep everything, mark unknown latency
        for start in range(0, len(configs), batch_size):
            yield [HealthResult(config=config, is_healthy=True) for config in configs[start : start + batch_size]]
        return

    # Community lists overlap heavily and one server usually carries many
    # configs, so the same (address, port) shows up over and over. Probing an
    # endpoint once and fanning the answer out to every config on it cuts the
    # work by roughly an order of magnitude - which is what makes it affordable
    # to check the whole pool instead of an arbitrary slice of it.
    endpoints = {(config.address, config.port) for config in configs}
    logger.info("free-configs: %d configs live on %d distinct endpoints", len(configs), len(endpoints))

    semaphore = asyncio.Semaphore(settings.max_concurrency)
    latencies: dict[tuple[str, int], int | None] = {}
    pending = list(endpoints)
    for start in range(0, len(pending), batch_size):
        chunk = pending[start : start + batch_size]
        results = await asyncio.gather(*[check_endpoint(address, port, semaphore) for address, port in chunk])
        latencies.update(zip(chunk, results))

    for start in range(0, len(configs), batch_size):
        batch = configs[start : start + batch_size]
        yield [
            HealthResult(
                config=config,
                is_healthy=latencies.get((config.address, config.port)) is not None,
                latency_ms=latencies.get((config.address, config.port)),
            )
            for config in batch
        ]
