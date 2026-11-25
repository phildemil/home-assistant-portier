"""Websocket API used by the overlay and the side panel."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import probatio as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util, slugify

from .config_flow import panel_options, telegram_chats
from .const import (
    CONF_CAMERA,
    CONF_PRELOAD,
    DEFAULTS,
    DOMAIN,
    SIGNAL_DISMISS,
    SIGNAL_RING,
    SIGNAL_UPDATE,
)
from .look import async_save_look, current_look
from .manager import PortierDoor, all_doors, door_for
from .preload import async_release_preload, async_set_preload

_LOGGER = logging.getLogger(__name__)


def _visible(connection: websocket_api.ActiveConnection, door: PortierDoor) -> bool:
    """A user sees a door if they may read its camera (or it has none)."""
    user = connection.user
    if user.is_admin or not door.camera:
        return True
    return user.permissions.check_entity(door.camera, POLICY_READ)


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_open)
    websocket_api.async_register_command(hass, ws_ring)
    websocket_api.async_register_command(hass, ws_dismiss)
    websocket_api.async_register_command(hass, ws_ring_missed)
    websocket_api.async_register_command(hass, ws_config)
    websocket_api.async_register_command(hass, ws_door_save)
    websocket_api.async_register_command(hass, ws_door_delete)
    websocket_api.async_register_command(hass, ws_look_save)
    websocket_api.async_register_command(hass, ws_still)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/still", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_still(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A still for the door card when Home Assistant's own one fails for this camera."""
    try:
        door = door_for(hass, msg["entry_id"])
    except HomeAssistantError:
        connection.send_error(msg["id"], "not_found", "Tür nicht gefunden")
        return
    if not door.camera or not _visible(connection, door):
        connection.send_error(msg["id"], "not_found", "Kein Kamerabild")
        return
    picture = await door.notifier.async_still()
    if not picture:
        connection.send_error(msg["id"], "no_picture", "Die Kamera liefert gerade kein Bild")
        return
    connection.send_result(
        msg["id"],
        {"content_type": picture[1], "data": base64.b64encode(picture[0]).decode("ascii")},
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Stream door snapshots and ring events to one client."""
    msg_id = msg["id"]
    is_admin = connection.user.is_admin

    def send(payload: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg_id, payload))

    def snapshot() -> dict[str, Any]:
        return {
            "type": "snapshot",
            "server_time": dt_util.utcnow().isoformat(),
            "frontend": hass.data.get(f"{DOMAIN}_frontend", {}).get("url"),
            "look": current_look(hass),
            "doors": [d.snapshot(is_admin) for d in all_doors(hass) if _visible(connection, d)],
        }

    @callback
    def on_ring(entry_id: str, payload: dict[str, Any]) -> None:
        try:
            door = door_for(hass, entry_id)
        except HomeAssistantError:
            return
        if _visible(connection, door):
            send({"type": "ring", "entry_id": entry_id, "server_time": dt_util.utcnow().isoformat(), **payload})

    @callback
    def on_update(_entry_id: str) -> None:
        send(snapshot())

    @callback
    def on_dismiss(entry_id: str, ring_time: str) -> None:
        send({"type": "dismiss", "entry_id": entry_id, "ring_time": ring_time})

    @callback
    def on_show(entry_id: str) -> None:
        send({"type": "show", "entry_id": entry_id})

    unsubs = [
        async_dispatcher_connect(hass, SIGNAL_RING, on_ring),
        async_dispatcher_connect(hass, SIGNAL_UPDATE, on_update),
        async_dispatcher_connect(hass, f"{DOMAIN}_show", on_show),
        async_dispatcher_connect(hass, SIGNAL_DISMISS, on_dismiss),
    ]

    @callback
    def unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg_id] = unsubscribe
    connection.send_result(msg_id)
    send(snapshot())


# The Home Assistant phone apps sign in with these client ids (Android, iOS, macOS).
COMPANION_CLIENT = "https://home-assistant.io/"


def _client_kind(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, hint: str | None
) -> str:
    """Home Assistant app or browser? The login of the connection tells it reliably."""
    token_id = getattr(connection, "refresh_token_id", None)
    token = hass.auth.async_get_refresh_token(token_id) if token_id else None
    client_id = (token.client_id or "") if token else ""
    if client_id.startswith(COMPANION_CLIENT):
        return "ha_app"
    if hint:
        return hint
    return "browser" if client_id else "app"


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/open",
        vol.Required("entry_id"): str,
        # Which kind of device pressed the button (shown in the history and live view).
        vol.Optional("client"): vol.In(["ha_app", "browser"]),
    }
)
@websocket_api.async_response
async def ws_open(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        door = door_for(hass, msg["entry_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    if not _visible(connection, door) or not door.can_open(connection.user.is_admin):
        connection.send_error(msg["id"], "unauthorized", "Keine Berechtigung zum Öffnen")
        return
    try:
        await door.async_open(context=connection.context(msg), via=_client_kind(hass, connection, msg.get("client")))
    except (HomeAssistantError, TimeoutError) as err:
        connection.send_error(msg["id"], "open_failed", str(err) or "Öffnen fehlgeschlagen")
        return
    connection.send_result(msg["id"], {"opened": True})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/ring", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_ring(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        door = door_for(hass, msg["entry_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    if not _visible(connection, door):
        connection.send_error(msg["id"], "unauthorized", "Keine Berechtigung")
        return
    await door.async_ring(source="test", context=connection.context(msg))
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/dismiss",
        vol.Required("entry_id"): str,
        vol.Required("ring_time"): str,
    }
)
@callback
def ws_dismiss(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A ring's live view was closed on one device – close it everywhere."""
    try:
        door = door_for(hass, msg["entry_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    if not _visible(connection, door):
        connection.send_error(msg["id"], "unauthorized", "Keine Berechtigung")
        return
    closed = door.async_dismiss(msg["ring_time"], context=connection.context(msg))
    connection.send_result(msg["id"], {"closed": closed})


_MISSED_REASONS = {
    "late": "Meldung kam zu spät an",
    "not_on_screen": "Seite war beim Klingeln nicht sichtbar",
}


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/ring_missed",
        vol.Required("entry_id"): str,
        vol.Required("reason"): vol.In(list(_MISSED_REASONS)),
        vol.Optional("age_ms"): vol.Any(None, int),
        vol.Optional("visible_ms"): vol.Any(None, int),
        vol.Optional("late"): bool,
        vol.Optional("app"): bool,
    }
)
@callback
def ws_ring_missed(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A page that was on screen did not show a ring: write why to the log."""
    try:
        door = door_for(hass, msg["entry_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    if not _visible(connection, door):
        connection.send_error(msg["id"], "unauthorized", "Keine Berechtigung")
        return
    age, visible = msg.get("age_ms"), msg.get("visible_ms")
    _LOGGER.warning(
        "Portier: Klingeln an %s auf einem Gerät von %s nicht angezeigt – %s "
        "(%s nach dem Klingeln angekommen, Seite sichtbar seit %s, %s%s)",
        door.name,
        connection.user.name if connection.user else "?",
        _MISSED_REASONS[msg["reason"]],
        f"{age / 1000:.1f} s" if age is not None else "?",
        f"{visible / 1000:.1f} s" if visible is not None else "-",
        "HA-App" if msg.get("app") else "Browser",
        ", nach Neuverbindung" if msg.get("late") else "",
    )
    connection.send_result(msg["id"])


# ---------------------------------------------------------------- door editing
def _nfc_tags(hass: HomeAssistant) -> list[dict[str, str]]:
    """NFC tags Home Assistant knows (scanned once with the app)."""
    tags = [
        {"entity_id": st.entity_id, "name": st.name, "tag_id": str(st.attributes.get("tag_id", ""))}
        for st in hass.states.async_all("tag")
    ]
    return sorted(tags, key=lambda t: t["name"].lower())


def _nfc_phones(hass: HomeAssistant) -> list[dict[str, str]]:
    """Phones with the Home Assistant app, as devices (the tag event names the device)."""
    reg = dr.async_get(hass)
    phones = [
        {"device_id": device.id, "name": device.name_by_user or device.name or entry.title}
        for entry in hass.config_entries.async_entries("mobile_app")
        for device in dr.async_entries_for_config_entry(reg, entry.entry_id)
    ]
    return sorted(phones, key=lambda p: p["name"].lower())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@websocket_api.require_admin
@callback
def ws_config(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Everything the door editor needs: current options and choices."""
    notify = sorted(
        s
        for s in hass.services.async_services_for_domain("notify")
        if s not in ("send_message", "persistent_notification")
    )
    # Friendly names for phones: the name set in the Companion App.
    labels: dict[str, str] = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        name = entry.data.get("device_name") or entry.title
        if name:
            labels[f"mobile_app_{slugify(name)}"] = name
    connection.send_result(
        msg["id"],
        {
            "defaults": DEFAULTS,
            "notify_labels": labels,
            "notify_services": notify,
            "telegram": hass.services.has_service("telegram_bot", "send_message"),
            "telegram_chats": telegram_chats(hass),
            "nfc_tags": _nfc_tags(hass),
            "nfc_phones": _nfc_phones(hass),
            "doors": {
                e.entry_id: {**DEFAULTS, **e.options, "name": e.options.get("name") or e.title}
                for e in hass.config_entries.async_entries(DOMAIN)
            },
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/door/save",
        vol.Optional("entry_id"): str,
        vol.Required("options"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_door_save(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create a new door or change an existing one."""
    entry_id = msg.get("entry_id")
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    if entry_id and (entry is None or entry.domain != DOMAIN):
        connection.send_error(msg["id"], "not_found", "Tür nicht gefunden")
        return
    options, errors = panel_options(
        hass, msg["options"], entry_id, dict(entry.options) if entry else {}
    )
    if errors:
        connection.send_error(msg["id"], "invalid", json.dumps(errors, ensure_ascii=False))
        return
    old = dict(entry.options) if entry else {}
    if entry is None:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=options
        )
        entry_id = result["result"].entry_id
    else:
        entry_id = entry.entry_id
        hass.config_entries.async_update_entry(entry, title=options["name"], options=options)
        await hass.config_entries.async_reload(entry_id)
    connection.send_result(msg["id"], {"entry_id": entry_id})
    # "Keep warm" is Home Assistant's own camera setting: the editor shows it and saving
    # sets it – only here, never behind the user's back at a restart. It runs after the
    # answer: the editor closes at once, whatever the camera takes.
    camera = options.get(CONF_CAMERA)
    if camera:
        await async_set_preload(hass, camera, bool(options.get(CONF_PRELOAD, True)))
    old_camera = old.get(CONF_CAMERA)
    if old_camera and old_camera != camera and old.get(CONF_PRELOAD, True):
        # The door no longer uses its old camera: stop keeping that one warm,
        # unless another door still needs it.
        await async_release_preload(hass, old_camera, "")


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/door/delete", vol.Required("entry_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_door_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "Tür nicht gefunden")
        return
    await hass.config_entries.async_remove(entry.entry_id)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/look/save",
        vol.Required("mode"): str,
        vol.Optional("accent"): str,
        vol.Optional("accent2"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_look_save(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The two accent colors, the same on every device."""
    raw = {k: msg[k] for k in ("mode", "accent", "accent2") if k in msg}
    try:
        look = await async_save_look(hass, raw)
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], look)
