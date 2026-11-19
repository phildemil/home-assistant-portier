"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import PortierDoor


class PortierEntity(Entity):
    """Entity bound to one Portier door."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, door: PortierDoor, key: str) -> None:
        self.door = door
        self._attr_translation_key = key
        self._attr_unique_id = f"{door.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, door.entry.entry_id)},
            name=door.name,
            manufacturer="Portier",
            model="Türklingel",
        )
