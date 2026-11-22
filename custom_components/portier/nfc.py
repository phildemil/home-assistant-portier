"""Open the door by holding a phone with the Home Assistant app to an NFC tag."""

from __future__ import annotations

from datetime import time
import logging
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import CONF_NFC_DEVICES, CONF_NFC_FROM, CONF_NFC_NOTIFY, CONF_NFC_TAGS, CONF_NFC_UNTIL

if TYPE_CHECKING:
    from .manager import PortierDoor

_LOGGER = logging.getLogger(__name__)

TAG_SCANNED = "tag_scanned"


def parse_time(value: str | None) -> time | None:
    """'07:00' or '07:00:00' -> time; empty or invalid -> None."""
    if not value:
        return None
    return dt_util.parse_time(str(value))


def in_window(now: time, start: time | None, end: time | None) -> bool:
    """True if now lies in [start, end); windows may run over midnight (22:00–06:00)."""
    if start is None and end is None:
        return True
    if start is None:
        return now < end
    if end is None:
        return now >= start
    if start <= end:
        return start <= now < end
    return now >= start or now < end


class NfcOpener:
    """Listens for scanned tags and opens the door for allowed phones."""

    def __init__(self, hass: HomeAssistant, door: PortierDoor) -> None:
        self.hass = hass
        self.door = door

    def async_listen(self) -> list[CALLBACK_TYPE]:
        return [self.hass.bus.async_listen(TAG_SCANNED, self._on_scan)]

    def _tag_ids(self) -> set[str]:
        """The configured tags, as tag ids (the option holds tag entities)."""
        ids: set[str] = set()
        for value in self.door.opt(CONF_NFC_TAGS) or []:
            state = self.hass.states.get(value)
            if state and state.attributes.get("tag_id"):
                ids.add(str(state.attributes["tag_id"]))
            elif not value.startswith("tag."):
                ids.add(value)  # a raw tag id also works
        return ids

    def _device_name(self, device_id: str | None) -> str | None:
        device = dr.async_get(self.hass).async_get(device_id) if device_id else None
        return (device.name_by_user or device.name) if device else None

    @callback
    def _on_scan(self, event: Event) -> None:
        tag_id = str(event.data.get("tag_id") or "")
        if not tag_id or not self.door.opener or tag_id not in self._tag_ids():
            return
        device_id = event.data.get("device_id")
        allowed = self.door.opt(CONF_NFC_DEVICES) or []
        if device_id not in allowed:
            _LOGGER.warning(
                "Portier: %s – NFC-Aufkleber von nicht freigegebenem Handy (%s) ignoriert",
                self.door.name,
                self._device_name(device_id) or device_id or "unbekannt",
            )
            return
        start = parse_time(self.door.opt(CONF_NFC_FROM))
        end = parse_time(self.door.opt(CONF_NFC_UNTIL))
        if not in_window(dt_util.now().time(), start, end):
            _LOGGER.info("Portier: %s – NFC außerhalb des Zeitfensters ignoriert", self.door.name)
            return
        self.hass.async_create_task(self._open(event, device_id))

    async def _open(self, event: Event, device_id: str | None) -> None:
        who = await self.door.user_name(event.context) or self._device_name(device_id)
        try:
            await self.door.async_open(context=event.context, via="nfc", who=who)
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.error("Portier: Öffnen per NFC fehlgeschlagen: %s", err)
            return
        if self.door.opt(CONF_NFC_NOTIFY):
            await self.door.notifier.async_info(
                f"🔓 {self.door.name}",
                f"{who or 'Jemand'} hat per NFC geöffnet · {dt_util.now().strftime('%H:%M')}",
            )
