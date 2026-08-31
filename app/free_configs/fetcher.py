"""Network side of the free-configs feature: fetch source lists, health-check URIs.

Everything here is async and bounded by a semaphore so a large pool cannot
starve the event loop the panel serves requests on.
"""

import asyncio
from dataclasses import dataclass

import aiohttp

from app.free_configs.parser import ParsedConfig, decode_body, parse_many
from app.utils.logger import get_logger
from config import free_configs_settings

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


async def fetch_sources(sources: list[tuple[int | None, str, bool]]) -> list[FetchResult]:
    """Fetch every source concurrently.

    ``sources`` is a list of ``(source_id, url, is_base64)`` tuples.
    """
    timeout = aiohttp.ClientTimeout(total=free_configs_settings.fetch_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [fetch_source(session, url, is_base64, source_id) for source_id, url, is_base64 in sources]
        return list(await asyncio.gather(*tasks))


async def check_config(config: ParsedConfig, semaphore: asyncio.Semaphore) -> HealthResult:
    """TCP-connect to the endpoint and measure how long it took.

    A successful connect only proves the port answers - it is not proof that the
    proxy protocol itself works. That caveat is surfaced in the API and docs.
    """
    async with semaphore:
        loop = asyncio.get_running_loop()
        started = loop.time()
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(config.address, config.port),
                timeout=free_configs_settings.tcp_timeout,
            )
            latency_ms = int((loop.time() - started) * 1000)
            return HealthResult(config=config, is_healthy=True, latency_ms=latency_ms)
        except (asyncio.TimeoutError, OSError, ValueError):
            return HealthResult(config=config, is_healthy=False)
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, asyncio.TimeoutError):
                    pass


async def check_configs(configs: list[ParsedConfig]) -> list[HealthResult]:
    """Health-check a batch of configs with bounded concurrency."""
    if not free_configs_settings.health_check:
        # health checking disabled: keep everything, mark unknown latency
        return [HealthResult(config=config, is_healthy=True) for config in configs]

    semaphore = asyncio.Semaphore(free_configs_settings.max_concurrency)
    return list(await asyncio.gather(*[check_config(config, semaphore) for config in configs]))
