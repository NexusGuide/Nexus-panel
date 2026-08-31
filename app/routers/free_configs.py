"""API for the free-configs add-on (fork feature).

Every endpoint is owner-only. Free configs inject third-party servers into user
subscriptions, which is a panel-wide trust decision, so it is deliberately not
delegated to sub-admins - and it also means this fork adds no new RBAC resource,
keeping the diff against upstream small.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from app.db import AsyncSession, get_db
from app.free_configs import crud, service
from app.free_configs.schemas import (
    FreeConfigResponse,
    FreeConfigsStatus,
    FreeConfigSourceCreate,
    FreeConfigSourceModify,
    FreeConfigSourceResponse,
    GroupAccessResponse,
    GroupAccessUpdate,
)
from app.models.admin import AdminDetails
from app.utils import responses
from config import free_configs_settings

from .authentication import require_owner

router = APIRouter(
    tags=["FreeConfigs"],
    prefix="/api/free-configs",
    responses={401: responses._401, 403: responses._403},
)


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #


@router.get("/sources", response_model=list[FreeConfigSourceResponse])
async def list_sources(db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)):
    """List configured free-config sources."""
    return await crud.get_sources(db)


@router.post("/sources", response_model=FreeConfigSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    new_source: FreeConfigSourceCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Add a source. The URL must be unique."""
    if await crud.get_source_by_url(db, new_source.url):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source URL already exists")
    return await crud.create_source(
        db, url=new_source.url, remark=new_source.remark, is_base64=new_source.is_base64
    )


@router.put("/sources/{source_id}", response_model=FreeConfigSourceResponse, responses={404: responses._404})
async def modify_source(
    source_id: int,
    modified: FreeConfigSourceModify,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Enable/disable a source or edit its remark."""
    source = await crud.get_source(db, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return await crud.update_source(
        db,
        source,
        remark=modified.remark,
        is_base64=modified.is_base64,
        is_enabled=modified.is_enabled,
    )


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: responses._404})
async def remove_source(
    source_id: int, db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)
):
    """Delete a source. Configs already harvested from it are kept until the next refresh."""
    source = await crud.get_source(db, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    await crud.delete_source(db, source)


# --------------------------------------------------------------------------- #
# pool
# --------------------------------------------------------------------------- #


@router.get("/status", response_model=FreeConfigsStatus)
async def get_status(db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)):
    """Feature settings, last refresh result, and current pool size."""
    pool = await crud.get_pool_stats(db)
    info = service.last_refresh_info()
    return FreeConfigsStatus(
        enabled=free_configs_settings.enabled,
        mode=free_configs_settings.mode,
        refresh_interval=free_configs_settings.refresh_interval,
        health_check=free_configs_settings.health_check,
        running=info["running"],
        last_refresh_at=info["last_refresh_at"],
        last_stats=info["last_stats"],
        pool_total=pool["total"],
        pool_healthy=pool["healthy"],
        pool_last_checked_at=pool["last_checked_at"],
    )


@router.get("/configs", response_model=list[FreeConfigResponse])
async def list_configs(
    healthy_only: bool = True,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Inspect the current pool (newest refresh), fastest endpoints first."""
    return await crud.get_configs(db, healthy_only=healthy_only, limit=limit)


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
async def trigger_refresh(_: AdminDetails = Depends(require_owner)):
    """Kick off a refresh in the background.

    A full run fetches every source and TCP-checks thousands of endpoints, so it
    is not awaited - poll ``GET /api/free-configs/status`` for the result.
    """
    if service.last_refresh_info()["running"]:
        return {"detail": "A refresh is already running"}
    asyncio.create_task(service.refresh_pool())
    return {"detail": "Refresh started"}


# --------------------------------------------------------------------------- #
# group access
# --------------------------------------------------------------------------- #


@router.get("/groups", response_model=GroupAccessResponse)
async def get_group_access(db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)):
    """Groups whose members receive free configs (used when FREE_CONFIGS_MODE=groups)."""
    return GroupAccessResponse(group_ids=await crud.get_enabled_group_ids(db))


@router.put("/groups", response_model=GroupAccessResponse)
async def set_group_access(
    payload: GroupAccessUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Replace the opted-in group list. Unknown group ids are ignored."""
    valid = await crud.set_group_access(db, payload.group_ids)
    await service.invalidate_access_cache()
    return GroupAccessResponse(group_ids=valid)
