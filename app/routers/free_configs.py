"""API for the free-configs add-on (fork feature).

Every endpoint is owner-only. Free configs inject third-party servers into user
subscriptions, which is a panel-wide trust decision, so it is deliberately not
delegated to sub-admins - and it also means this fork adds no new RBAC resource,
keeping the diff against upstream small.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.db import AsyncSession, get_db
from app.free_configs import crud, service, settings as settings_module
from app.free_configs.fields import ConfigFieldsError, as_form, build as build_uri
from app.free_configs.parser import parse_uri
from app.free_configs.schemas import (
    BulkOverrideUpdate,
    ConfigFieldsResponse,
    ConfigFieldsUpdate,
    FreeConfigOverrideUpdate,
    FreeConfigPage,
    FreeConfigResponse,
    FreeConfigSettingsResponse,
    FreeConfigSettingsUpdate,
    FreeConfigsStatus,
    FreeConfigSourceCreate,
    FreeConfigSourceModify,
    FreeConfigSourceResponse,
    ManualConfigCreate,
)
from app.free_configs.schemas import (
    GroupAccessOne,
    GroupAccessResponse,
    GroupAccessUpdate,
    GroupFreeConfigState,
    GroupAssignmentPatch,
    GroupAssignmentUpdate,
    GroupSummary,
)
from app.models.admin import AdminDetails
from app.utils import responses
from config import free_configs_settings as env_settings

from .authentication import require_owner

router = APIRouter(
    tags=["FreeConfigs"],
    prefix="/api/free-configs",
    responses={401: responses._401, 403: responses._403},
)

# The admin page is served separately from the API, without the /api prefix and
# without an auth dependency: it is a static shell containing no data at all.
# Every byte it displays is fetched by its own JavaScript from the owner-only
# endpoints above, using the token the dashboard already holds in localStorage
# on this same origin. So there is nothing to leak by serving the HTML itself,
# and nothing to see without an owner's token.
page_router = APIRouter(tags=["FreeConfigs"], include_in_schema=False)

_PANEL_HTML = Path(__file__).resolve().parent.parent / "free_configs" / "static" / "panel.html"


@page_router.get("/free-configs/panel", response_class=HTMLResponse)
async def free_configs_panel():
    """The Free Configs admin page, embedded by the dashboard."""
    try:
        return HTMLResponse(_PANEL_HTML.read_text(encoding="utf-8"))
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The free-configs panel page is missing from this image",
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
    effective = await settings_module.get_settings()
    return FreeConfigsStatus(
        enabled=effective.enabled,
        mode=effective.mode,
        refresh_interval=effective.refresh_interval,
        health_check=effective.health_check,
        running=info["running"],
        last_refresh_at=info["last_refresh_at"],
        last_stats=info["last_stats"],
        pool_total=pool["total"],
        pool_healthy=pool["healthy"],
        pool_disabled=pool["disabled"],
        pool_manual=pool["manual"],
        pool_last_checked_at=pool["last_checked_at"],
    )


def _to_response(config, override) -> FreeConfigResponse:
    return FreeConfigResponse(
        id=config.id,
        uri=config.uri,
        uri_hash=config.uri_hash,
        protocol=config.protocol,
        address=config.address,
        port=config.port,
        is_healthy=config.is_healthy,
        is_enabled=config.is_enabled,
        is_manual=config.is_manual,
        remark=override.remark if override else None,
        note=override.note if override else None,
        latency_ms=config.latency_ms,
        last_checked_at=config.last_checked_at,
    )


@router.get("/configs", response_model=FreeConfigPage)
async def list_configs(
    search: str | None = None,
    protocol: str | None = None,
    status_filter: str = Query(default="all", alias="status", pattern="^(all|enabled|disabled|manual|unhealthy)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """A page of the pool, fastest first, with each config's admin override."""
    rows, total = await crud.get_configs_page(
        db, search=search, protocol=protocol, status=status_filter, offset=offset, limit=limit
    )
    return FreeConfigPage(
        items=[_to_response(config, override) for config, override in rows],
        total=total,
        offset=offset,
        limit=limit,
        protocols=await crud.get_protocols(db),
    )


@router.put("/configs/{uri_hash}", response_model=FreeConfigResponse)
async def update_config(
    uri_hash: str,
    payload: FreeConfigOverrideUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Switch one config off or on, rename it, or attach a note.

    Stored against the config's content hash rather than its row id, so the
    decision survives the next refresh - which replaces the pool wholesale.
    """
    await crud.set_override(
        db, uri_hash, is_enabled=payload.is_enabled, remark=payload.remark, note=payload.note
    )
    await service.invalidate_pool_cache()

    override = await crud.get_override(db, uri_hash)
    config = await crud.get_config_by_hash(db, uri_hash)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No config with that hash in the pool")
    return _to_response(config, override)


@router.get("/configs/{uri_hash}/fields", response_model=ConfigFieldsResponse)
async def get_config_fields(
    uri_hash: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Break one config into editable fields - address, port, UUID, SNI, and the rest."""
    config = await crud.get_config_by_hash(db, uri_hash)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No config with that hash in the pool")
    try:
        form = as_form(config.uri)
    except ConfigFieldsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    override = await crud.get_override(db, uri_hash)
    if override is not None and override.remark:
        form["alias"] = override.remark
    return ConfigFieldsResponse(**form)


@router.put("/configs/{uri_hash}/fields", response_model=FreeConfigResponse)
async def update_config_fields(
    uri_hash: str,
    payload: ConfigFieldsUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Save an edited config.

    Changing an address, credential or SNI makes this a different proxy, so the
    result is stored as a manual entry and the original is switched off. Editing
    only the name leaves the config where it was.
    """
    existing = await crud.get_config_by_hash(db, uri_hash)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No config with that hash in the pool")

    try:
        uri = build_uri(existing.protocol, payload.alias, payload.address, payload.port, dict(payload.params))
    except ConfigFieldsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    parsed = parse_uri(uri)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Those values do not make a usable config - check the address and port",
        )

    config = await crud.replace_with_edited(db, uri_hash, parsed, remark=payload.alias or None)
    await service.invalidate_pool_cache()
    return _to_response(config, await crud.get_override(db, parsed.uri_hash))


@router.post("/configs/bulk", status_code=status.HTTP_200_OK)
async def bulk_update_configs(
    payload: BulkOverrideUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Switch many configs on or off at once."""
    changed = await crud.set_overrides_bulk(db, payload.uri_hashes, payload.is_enabled)
    await service.invalidate_pool_cache()
    return {"changed": changed}


@router.post("/configs/manual", response_model=FreeConfigResponse, status_code=status.HTTP_201_CREATED)
async def add_manual_config(
    payload: ManualConfigCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Add a config by hand. It is kept across refreshes and never capped out."""
    parsed = parse_uri(payload.uri)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not parse that URI - check the scheme, host and port",
        )
    config = await crud.upsert_manual_config(db, parsed, remark=payload.remark)
    await service.invalidate_pool_cache()
    return _to_response(config, await crud.get_override(db, parsed.uri_hash))


@router.delete("/configs/{uri_hash}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_override(
    uri_hash: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Forget an override. A manual config is removed; a harvested one is re-enabled."""
    if not await crud.clear_override(db, uri_hash):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No override for that hash")
    await service.invalidate_pool_cache()


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #


@router.get("/settings", response_model=FreeConfigSettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)):
    """Effective settings, what is stored, and what .env would give on its own."""
    stored = await settings_module.read_row(db)
    effective = await settings_module.get_settings()
    return FreeConfigSettingsResponse(
        effective=effective.__dict__,
        stored={**stored["stored"], "disabled": stored["disabled"]},
        defaults=stored["defaults"],
        env_enabled=env_settings.enabled,
    )


@router.put("/settings", response_model=FreeConfigSettingsResponse)
async def update_settings(
    payload: FreeConfigSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Save settings. A field sent as null goes back to its .env default.

    ``FREE_CONFIGS_ENABLED`` is not here: the panel can switch the feature off
    but never on, so an install that never opted in cannot be enabled from a
    web form.
    """
    await settings_module.update_settings(db, payload.model_dump(exclude_unset=True))
    await service.invalidate_pool_cache()
    stored = await settings_module.read_row(db)
    effective = await settings_module.get_settings()
    return FreeConfigSettingsResponse(
        effective=effective.__dict__,
        stored={**stored["stored"], "disabled": stored["disabled"]},
        defaults=stored["defaults"],
        env_enabled=env_settings.enabled,
    )


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


@router.get("/groups/summary", response_model=list[GroupSummary])
async def list_group_summaries(db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)):
    """Every panel group, whether it receives free configs, and how many it has."""
    opted_in = set(await crud.get_enabled_group_ids(db))
    counts = await crud.get_assignment_counts(db)
    return [
        GroupSummary(
            id=group_id,
            name=name,
            receives_free_configs=group_id in opted_in,
            assigned_count=counts.get(group_id, 0),
            # an opted-in group with no explicit list gets everything, which is
            # what "opted in" meant before assignment existed
            gets_whole_pool=group_id in opted_in and counts.get(group_id, 0) == 0,
        )
        for group_id, name in await crud.list_groups(db)
    ]


@router.get("/groups/{group_id}/state", response_model=GroupFreeConfigState)
async def get_group_state(
    group_id: int, db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)
):
    """One group's free-config settings, for the panel's own group dialog."""
    enabled = group_id in set(await crud.get_enabled_group_ids(db))
    hashes = await crud.get_group_assignments(db, group_id)
    return GroupFreeConfigState(
        group_id=group_id, enabled=enabled, uri_hashes=hashes, gets_whole_pool=enabled and not hashes
    )


@router.put("/groups/{group_id}/access", response_model=GroupFreeConfigState)
async def set_group_access_one(
    group_id: int,
    payload: GroupAccessOne,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Opt one group in or out, leaving every other group alone."""
    try:
        await crud.set_group_access_one(db, group_id, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await service.invalidate_access_cache()
    await service.invalidate_pool_cache()
    hashes = await crud.get_group_assignments(db, group_id)
    return GroupFreeConfigState(
        group_id=group_id, enabled=payload.enabled, uri_hashes=hashes,
        gets_whole_pool=payload.enabled and not hashes,
    )


@router.get("/groups/{group_id}/configs", response_model=list[str])
async def get_group_configs(
    group_id: int, db: AsyncSession = Depends(get_db), _: AdminDetails = Depends(require_owner)
):
    """The config hashes assigned to this group. Empty means it gets the whole pool."""
    return await crud.get_group_assignments(db, group_id)


@router.put("/groups/{group_id}/configs", response_model=dict)
async def set_group_configs(
    group_id: int,
    payload: GroupAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Replace this group's config list, the way a group's inbounds are set.

    An empty list restores "this group gets the whole pool".
    """
    count = await crud.set_group_assignments(db, group_id, payload.uri_hashes)
    await service.invalidate_access_cache()
    await service.invalidate_pool_cache()
    return {"group_id": group_id, "assigned": count, "gets_whole_pool": count == 0}


@router.post("/groups/{group_id}/configs", response_model=dict)
async def patch_group_configs(
    group_id: int,
    payload: GroupAssignmentPatch,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Add or remove configs for one group without replacing its whole list."""
    if payload.action == "add":
        changed = await crud.add_group_assignments(db, group_id, payload.uri_hashes)
    else:
        changed = await crud.remove_group_assignments(db, group_id, payload.uri_hashes)
    await service.invalidate_access_cache()
    await service.invalidate_pool_cache()
    remaining = len(await crud.get_group_assignments(db, group_id))
    return {"group_id": group_id, "changed": changed, "assigned": remaining, "gets_whole_pool": remaining == 0}


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
