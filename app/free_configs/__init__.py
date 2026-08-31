"""Free-configs add-on (fork feature).

Harvests proxy URIs from public community lists, health-checks them, and appends
the healthy ones to the subscription output of opted-in users.

Importing this package registers the feature's tables on the shared
``Base.metadata``.
"""

from app.free_configs.models import FreeConfig, FreeConfigGroupAccess, FreeConfigSource
from app.free_configs.service import (
    get_configs_for_user,
    last_refresh_info,
    refresh_pool,
    user_is_eligible,
)

__all__ = [
    "FreeConfig",
    "FreeConfigGroupAccess",
    "FreeConfigSource",
    "get_configs_for_user",
    "last_refresh_info",
    "refresh_pool",
    "user_is_eligible",
]
