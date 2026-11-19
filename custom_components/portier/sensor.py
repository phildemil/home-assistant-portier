"""Sensors: last ring, last opened."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import SIGNAL_UPDATE
from .entity import PortierEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    door = entry.runtime_data
    entities = [TimeSensor(door, "last_ring", "last_ring")]
    if door.opener:
        entities.append(TimeSensor(door, "last_opened", "last_opened"))
    async_add_entities(entities)


class TimeSensor(PortierEntity, SensorEntity, RestoreEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, door, key: str, field: str) -> None:
        super().__init__(door, key)
        self._field = field
        self._restored: datetime | None = None

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) and last.state not in (
            None,
            "unknown",
            "unavailable",
        ):
            self._restored = dt_util.parse_datetime(last.state)
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._on_update)
        )

    @callback
    def _on_update(self, entry_id: str) -> None:
        if entry_id == self.door.entry.entry_id:
            self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        return getattr(self.door.state, self._field) or self._restored
