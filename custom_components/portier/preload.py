"""Keep the door camera's stream warm, so the live picture is there the moment it rings.

This is Home Assistant's own "preload stream" camera setting. Home Assistant starts
preloaded streams only when it starts, so Portier also starts the stream right away.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_CAMERA, CONF_PRELOAD, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_set_preload(hass: HomeAssistant, entity_id: str | None, on: bool) -> None:
    """Switch "preload stream" for a camera on or off. Never raises."""
    if not entity_id or "camera" not in hass.config.components:
        return
    try:
        # Imported only here: the camera component (and its libraries) is loaded by now.
        from homeassistant.components.camera import CameraEntityFeature  # noqa: PLC0415
        from homeassistant.components.camera.const import (  # noqa: PLC0415
            DATA_CAMERA_PREFS,
            DATA_COMPONENT,
        )

        prefs = hass.data.get(DATA_CAMERA_PREFS)
        if prefs is None:
            return
        current = await prefs.get_dynamic_stream_settings(entity_id)
        if current.preload_stream != on:
            await prefs.async_update(entity_id, preload_stream=on)
        component = hass.data.get(DATA_COMPONENT)
        camera = component.get_entity(entity_id) if component else None
        if camera is None:
            return  # not there yet: Home Assistant preloads it itself once it has started
        if provider := camera.webrtc_provider:
            await provider.async_on_camera_prefs_update(camera)
        if on and camera.supported_features & CameraEntityFeature.STREAM:
            if stream := await camera.async_create_stream():
                stream.add_provider("hls")
                await stream.start()
    except Exception as err:  # noqa: BLE001 – a convenience: never break a door over it
        _LOGGER.warning("Portier: Stream vorladen für %s nicht möglich: %s", entity_id, err)


async def async_release_preload(hass: HomeAssistant, entity_id: str | None, leaving: str) -> None:
    """A door stops using this camera (or its preload): switch it off unless another door needs it."""
    if not entity_id:
        return
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == leaving:
            continue
        if entry.options.get(CONF_CAMERA) == entity_id and entry.options.get(CONF_PRELOAD, True):
            return
    await async_set_preload(hass, entity_id, False)
