"""Portier – video doorbell for Home Assistant."""

from __future__ import annotations

import probatio as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .const import (
    ACTION_WINDOW_MAX,
    CONF_ACTION_WINDOW,
    DOMAIN,
    OLD_ACTION_WINDOW_MINUTES,
    PLATFORMS,
    SIGNAL_UPDATE,
)
from .frontend import async_door_added, async_register_frontend, async_remove_loader
from .look import async_load_look, async_remove_look
from .manager import PortierDoor, all_doors, door_for
from .websocket import async_register_websocket

type PortierConfigEntry = ConfigEntry[PortierDoor]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_ENTRY_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})


def _doors_for_call(hass: HomeAssistant, call: ServiceCall) -> list[PortierDoor]:
    if entry_id := call.data.get("entry_id"):
        return [door_for(hass, entry_id)]
    return all_doors(hass)


async def _require_admin(hass: HomeAssistant, call: ServiceCall) -> bool:
    """Return True if the caller is an admin (or an automation without user)."""
    if call.context.user_id is None:
        return True
    user = await hass.auth.async_get_user(call.context.user_id)
    return bool(user and user.is_active and user.is_admin)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services and the websocket API once."""
    await async_load_look(hass)
    async_register_websocket(hass)
    # The panel stays available as long as Portier is loaded, even with no door left,
    # so doors can be added right from it.
    await async_register_frontend(hass)

    async def ring(call: ServiceCall) -> None:
        for door in _doors_for_call(hass, call):
            await door.async_ring(source="test", context=call.context)

    async def open_door(call: ServiceCall) -> None:
        is_admin = await _require_admin(hass, call)
        for door in _doors_for_call(hass, call):
            if not door.can_open(is_admin):
                raise Unauthorized(context=call.context)
            await door.async_open(context=call.context, via="service")

    async def show(call: ServiceCall) -> None:
        for door in _doors_for_call(hass, call):
            async_dispatcher_send(hass, f"{DOMAIN}_show", door.entry.entry_id)

    hass.services.async_register(DOMAIN, "ring", ring, schema=_ENTRY_SCHEMA)
    hass.services.async_register(DOMAIN, "open", open_door, schema=_ENTRY_SCHEMA)
    hass.services.async_register(DOMAIN, "show", show, schema=_ENTRY_SCHEMA)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: PortierConfigEntry) -> bool:
    """1.1 -> 1.2: the open-button window is given in seconds (max 2 min)."""
    if entry.version == 1 and entry.minor_version < 2:
        options = dict(entry.options)
        minutes = options.pop(OLD_ACTION_WINDOW_MINUTES, None)
        if minutes is not None and CONF_ACTION_WINDOW not in options:
            options[CONF_ACTION_WINDOW] = min(float(minutes) * 60, ACTION_WINDOW_MAX)
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PortierConfigEntry) -> bool:
    door = PortierDoor(hass, entry)
    await door.async_setup()
    entry.runtime_data = door
    await async_door_added(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_dispatcher_send(hass, SIGNAL_UPDATE, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PortierConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_unload()
        entry.runtime_data = None
        async_dispatcher_send(hass, SIGNAL_UPDATE, entry.entry_id)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: PortierConfigEntry) -> None:
    """Last door removed: take Portier's line out of configuration.yaml again."""
    others = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]
    if not others:
        await async_remove_loader(hass)
        await async_remove_look(hass)
