"""The single hook the subscription pipeline calls into.

Kept in this package (rather than inline in ``app/subscription/share.py``) so the
upstream file only gains an import and one call - which keeps rebasing this fork
onto new upstream releases cheap.

Scope note: free configs are pre-built URIs owned by third parties, so they can
only be emitted verbatim into the raw-link formats (``links`` / ``links_base64``).
Structured formats - clash, sing-box, xray, outline, wireguard - describe
outbounds field by field and cannot carry a foreign URI without being parsed into
their own schema, so they are skipped. This is the same limitation upstream's own
``EXTERNAL_CONFIG`` has.
"""

from app.free_configs.service import get_configs_for_user
from app.free_configs.settings import get_settings
from app.utils.logger import get_logger
from config import free_configs_settings

# Which user states each policy still serves. Free-config traffic never reaches
# this panel, so it cannot be metered: a user who keeps receiving them after
# running out keeps working indefinitely. That is why the default is the
# strictest option, and why the looser ones exist at all - a panel whose whole
# offering is free configs may deliberately want them.
SERVED_STATES = {
    "active": {"active", "on_hold"},
    "not_disabled": {"active", "on_hold", "limited", "expired"},
    "everyone": {"active", "on_hold", "limited", "expired", "disabled"},
}

logger = get_logger("free-configs")


async def append_free_configs(conf, user) -> int:
    """Append this user's free configs to ``conf``. Returns how many were added.

    Never raises: a problem with the free pool must not break a paying user's
    subscription.
    """
    if not free_configs_settings.enabled:
        return 0

    try:
        policy = (await get_settings()).serve_to
        status = getattr(user, "status", None)
        status = getattr(status, "value", status)
        if status is not None and str(status) not in SERVED_STATES.get(policy, SERVED_STATES["active"]):
            return 0
    except Exception as exc:  # noqa: BLE001 - never break a subscription over this
        logger.debug("could not apply the free-configs user policy: %s", exc)

    # Only raw-link renderers can accept a pre-built URI (duck-typed on purpose:
    # no import of the renderer classes, so there is no import cycle risk).
    add_link = getattr(conf, "add_link", None)
    if not callable(add_link):
        return 0

    try:
        uris = await get_configs_for_user(user.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("could not load free configs for user %s: %s", getattr(user, "id", "?"), exc)
        return 0

    for uri in uris:
        add_link(uri)

    if uris:
        logger.debug("appended %d free configs for user %s", len(uris), user.id)
    return len(uris)
