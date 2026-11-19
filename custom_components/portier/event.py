"""Ring event entity – handy for your own automations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_RING
from .entity import PortierEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([RingEvent(entry.runtime_data)])


class RingEvent(PortierEntity, EventEntity):
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = ["ring"]

    def __init__(self, door) -> None:
        super().__init__(door, "ring")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_RING, self._on_ring)
        )

    @callback
    def _on_ring(self, entry_id: str, payload: dict[str, Any]) -> None:
        if entry_id != self.door.entry.entry_id:
            return
        source = self.door.state.history[0].source if self.door.state.history else "trigger"
        self._trigger_event("ring", {"source": source})
        self.async_write_ha_state()
