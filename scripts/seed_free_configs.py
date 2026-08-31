#!/usr/bin/env python3
"""Seed the free-configs source list with well-known public community lists.

Usage (from the repo root, with the panel's virtualenv active):

    python3 scripts/seed_free_configs.py            # add the defaults
    python3 scripts/seed_free_configs.py --list     # show what is configured
    python3 scripts/seed_free_configs.py --clear    # remove every source

Sources are ordinary rows: you can add, disable, or delete them at any time
through /api/free-configs/sources instead of using this script.

These URLs point at third-party community projects. They are aggregators of
free, publicly posted proxies - nobody involved operates the servers behind
them, and they can change or disappear without notice. Review the list before
seeding it, and see FREE_CONFIGS.md for the caveats that come with serving them.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import GetDB  # noqa: E402
from app.free_configs import crud  # noqa: E402

# (url, is_base64, remark)
DEFAULT_SOURCES = [
    ("https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/verified/configs.txt", False, "0xRadikal"),
    ("https://raw.githubusercontent.com/itsyebekhe/PSG/main/subscriptions/xray/mix", False, "PSG mix"),
    (
        "https://github.com/Delta-Kronecker/V2ray-Config/raw/refs/heads/main/config/all_configs.txt",
        False,
        "Delta-Kronecker",
    ),
    (
        "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/refs/heads/main/mtn/sub_1.txt",
        False,
        "MahsaFreeConfig",
    ),
    ("https://raw.githubusercontent.com/iampedii/whitedns-sub/refs/heads/main/base64.txt", True, "whitedns"),
    ("https://openproxylist.com/v2ray/rawlist/text", False, "openproxylist.com"),
    (
        "https://raw.githubusercontent.com/4n0nymou3/multi-proxy-config-fetcher/refs/heads/main/configs/proxy_configs_tested.txt",
        False,
        "4n0nymou3 (tested)",
    ),
    ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY_RAW.txt", False, "roosterkid"),
    (
        "https://raw.githubusercontent.com/iampedii/whitedns-sub/refs/heads/main/cloudflare-base64.txt",
        True,
        "whitedns cloudflare",
    ),
]


async def seed() -> None:
    added = skipped = 0
    async with GetDB() as db:
        for url, is_base64, remark in DEFAULT_SOURCES:
            if await crud.get_source_by_url(db, url):
                print(f"  exists  {url}")
                skipped += 1
                continue
            await crud.create_source(db, url=url, remark=remark, is_base64=is_base64)
            print(f"  added   {url}")
            added += 1
    print(f"\n{added} added, {skipped} already present.")
    print("Run a refresh with: POST /api/free-configs/refresh   (or wait for the scheduled job)")


async def show() -> None:
    async with GetDB() as db:
        sources = await crud.get_sources(db)
        stats = await crud.get_pool_stats(db)
    if not sources:
        print("No sources configured.")
    for source in sources:
        state = "enabled " if source.is_enabled else "disabled"
        b64 = " [base64]" if source.is_base64 else ""
        last = f" last={source.last_fetch_count}" if source.last_fetch_at else ""
        err = f" error={source.last_error}" if source.last_error else ""
        print(f"  [{source.id}] {state}{b64} {source.url}{last}{err}")
    print(f"\nPool: {stats['healthy']} healthy / {stats['total']} total (last checked {stats['last_checked_at']})")


async def clear() -> None:
    async with GetDB() as db:
        sources = await crud.get_sources(db)
        for source in sources:
            await crud.delete_source(db, source)
    print(f"Removed {len(sources)} source(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="show configured sources and pool stats")
    group.add_argument("--clear", action="store_true", help="delete every configured source")
    args = parser.parse_args()

    if args.list:
        asyncio.run(show())
    elif args.clear:
        asyncio.run(clear())
    else:
        asyncio.run(seed())


if __name__ == "__main__":
    main()
