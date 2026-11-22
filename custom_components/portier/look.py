"""Portier's two accent colors – one setting for every device."""

from __future__ import annotations

from typing import Any

import probatio as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SIGNAL_UPDATE

_KEY = f"{DOMAIN}_look"
STORE_KEY = f"{DOMAIN}.look"

MODE_DEFAULT = "default"  # Portier's brass and green
MODE_SYSTEM = "system"  # the colors of the Home Assistant theme
MODE_CUSTOM = "custom"  # two colors picked by an administrator

DEFAULT_LOOK: dict[str, str] = {
    "mode": MODE_DEFAULT,
    "accent": "#C9A44C",
    "accent2": "#4FA38A",
}

_HEX = vol.Match(r"^#[0-9a-fA-F]{6}$")
LOOK_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In([MODE_DEFAULT, MODE_SYSTEM, MODE_CUSTOM]),
        vol.Optional("accent"): _HEX,
        vol.Optional("accent2"): _HEX,
    }
)


async def async_load_look(hass: HomeAssistant) -> None:
    store: Store[dict[str, Any]] = Store(hass, 1, STORE_KEY)
    saved = await store.async_load() or {}
    try:
        look = {**DEFAULT_LOOK, **LOOK_SCHEMA(saved)}
    except vol.Invalid:
        look = dict(DEFAULT_LOOK)
    hass.data[_KEY] = {"store": store, "look": look}


def current_look(hass: HomeAssistant) -> dict[str, str]:
    return dict(hass.data.get(_KEY, {}).get("look") or DEFAULT_LOOK)


async def async_save_look(hass: HomeAssistant, raw: dict[str, Any]) -> dict[str, str]:
    """Validate, store and send the new colors to every open page."""
    look = {**current_look(hass), **LOOK_SCHEMA(raw)}
    if look["mode"] == MODE_DEFAULT:
        look = dict(DEFAULT_LOOK)  # "back to default" also resets the picked colors
    data = hass.data[_KEY]
    data["look"] = look
    await data["store"].async_save(look)
    async_dispatcher_send(hass, SIGNAL_UPDATE, "")
    return look


async def async_remove_look(hass: HomeAssistant) -> None:
    """Portier is being removed: forget the colors too."""
    data = hass.data.get(_KEY)
    if data:
        await data["store"].async_remove()
        data["look"] = dict(DEFAULT_LOOK)
