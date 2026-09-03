"""
Admin CLI Module — generate-temp-key only
"""

import asyncio

from app.db.base import GetDB
from app.db.crud.temp_key import create_temp_key
from cli import console


async def _generate_temp_key():
    async with GetDB() as db:
        key = await create_temp_key(db)
        # The key is the one thing being read off the screen and typed into a
        # browser, so it gets the loud colour; everything around it is context.
        console.print(f"Temp key: [bold yellow]{key.key}[/bold yellow]")
        console.print(f"[dim]Expires at {key.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')} - "
                      f"valid for 5 minutes, single use.[/dim]")


def generate_temp_key():
    """Generate a one-time temp key for owner setup operations."""
    asyncio.run(_generate_temp_key())
