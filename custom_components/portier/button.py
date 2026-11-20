"""Buttons: open door, test ring."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import PortierEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    door = entry.runtime_data
    entities: list[ButtonEntity] = [TestRingButton(door)]
    if door.opener:
        entities.append(OpenButton(door))
    async_add_entities(entities)


class OpenButton(PortierEntity, ButtonEntity):
    _attr_icon = "mdi:door-open"

    def __init__(self, door) -> None:
        super().__init__(door, "open")

    async def async_press(self) -> None:
        await self.door.async_open(context=self._context, via="button")


class TestRingButton(PortierEntity, ButtonEntity):
    _attr_icon = "mdi:bell-ring"

    def __init__(self, door) -> None:
        super().__init__(door, "test_ring")

    async def async_press(self) -> None:
        await self.door.async_ring(source="test", context=self._context)
