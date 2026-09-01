from fastapi import APIRouter

from . import (
    admin,
    admin_role,
    api_key,
    client_template,
    core,
    free_configs,
    group,
    home,
    host,
    hwid,
    node,
    settings,
    setup,
    subscription,
    system,
    user,
    user_template,
)

api_router = APIRouter()

routers = [
    home.router,
    admin.router,
    api_key.router,
    admin_role.router,
    setup.router,
    system.router,
    settings.router,
    group.router,
    core.router,
    client_template.router,
    host.router,
    node.router,
    user.router,
    subscription.router,
    user_template.router,
    hwid.router,
    free_configs.router,
    # the admin page itself: static HTML, no /api prefix, no data in the shell
    free_configs.page_router,
]

for router in routers:
    api_router.include_router(router)

__all__ = ["api_router"]
