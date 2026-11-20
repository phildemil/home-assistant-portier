"""Runtime logic for one Portier door."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.lock import LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADMIN_ONLY,
    CONF_AFTER_OPEN_SECONDS,
    CONF_CAMERA,
    CONF_CONTACT,
    CONF_DEBOUNCE,
    CONF_EVENT_TYPES,
    CONF_GONG,
    CONF_GONG_SECONDS,
    CONF_LIVE_SECONDS,
    CONF_NAME,
    CONF_OPENER,
    CONF_PULSE_MODE,
    CONF_PULSE_SECONDS,
    CONF_SOUND,
    CONF_TALK,
    CONF_TRIGGER,
    CONF_TRIGGER_EDGE,
    CONF_VOLUME,
    DEFAULTS,
    DOMAIN,
    EDGE_BOTH,
    EDGE_DOMAINS,
    EDGE_OFF,
    EDGE_ON,
    EVENT_OPENED,
    EVENT_RING,
    HISTORY_SIZE,
    OPEN_HISTORY_SIZE,
    PULSE_DEVICE,
    PULSE_DOMAINS,
    SIGNAL_DISMISS,
    SIGNAL_RING,
    SIGNAL_UPDATE,
    EVENT_DISMISSED,
)
from .notifications import Notifier

_LOGGER = logging.getLogger(__name__)

_INVALID = (None, STATE_UNAVAILABLE, STATE_UNKNOWN)
_SWITCH_TIMEOUT = 10


@dataclass
class RingRecord:
    """A single ring: when, how (trigger/test), by which button or person."""

    time: datetime
    source: str
    who: str | None = None
    entity: str | None = None


@dataclass
class OpenRecord:
    """A single opening: when, which way (app, push, telegram, button, service), by whom."""

    time: datetime
    via: str
    who: str | None = None


@dataclass
class DoorState:
    """Volatile state shared with frontends."""

    history: deque[RingRecord] = field(default_factory=lambda: deque(maxlen=HISTORY_SIZE))
    opens: deque[OpenRecord] = field(default_factory=lambda: deque(maxlen=OPEN_HISTORY_SIZE))
    ring_count: int = 0
    last_ring: datetime | None = None
    last_opened: datetime | None = None
    view_until: datetime | None = None
    ring_id: int = 0


class PortierDoor:
    """Connects trigger, camera, opener and notifications of one door."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.state = DoorState()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._pulse_cancel: CALLBACK_TYPE | None = None
        self._gong_cancel: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._pending_off: set[str] = set()
        self._last_trigger: datetime | None = None
        self.notifier = Notifier(hass, self)

    # ------------------------------------------------------------------ config
    def opt(self, key: str) -> Any:
        """Read an option with default."""
        return self.entry.options.get(key, DEFAULTS.get(key))

    @property
    def name(self) -> str:
        return self.entry.options.get(CONF_NAME) or self.entry.title

    @property
    def camera(self) -> str | None:
        return self.opt(CONF_CAMERA)

    @property
    def opener(self) -> str | None:
        return self.opt(CONF_OPENER)

    @property
    def contact(self) -> str | None:
        return self.opt(CONF_CONTACT)

    # --------------------------------------------------------------- lifecycle
    async def async_setup(self) -> None:
        """Recover unfinished pulses and start listening."""
        stored = await self._store.async_load() or {}
        for item in stored.get("opens", []):
            try:
                self.state.opens.append(
                    OpenRecord(dt_util.parse_datetime(item["time"]), item["via"], item.get("who"))
                )
            except (KeyError, TypeError, ValueError):
                continue
        if self.state.opens and self.state.opens[0].time:
            self.state.last_opened = self.state.opens[0].time
        for entity_id in stored.get("pending_off", []):
            _LOGGER.warning("Portier: switching %s off after restart", entity_id)
            self._pending_off.add(entity_id)
            await self._turn_off(entity_id)

        if trigger := self.opt(CONF_TRIGGER):
            self._unsubs.append(
                async_track_state_change_event(self.hass, [trigger], self._handle_trigger)
            )
        watched = [e for e in (self.contact, self.opener) if e]
        if watched:
            self._unsubs.append(
                async_track_state_change_event(self.hass, watched, self._handle_watched)
            )
        self._unsubs.extend(self.notifier.async_listen())

    async def async_unload(self) -> None:
        """Stop listening and make sure nothing stays switched on."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for cancel in (self._pulse_cancel, self._gong_cancel):
            if cancel:
                cancel()
        for entity_id in list(self._pending_off):
            await self._turn_off(entity_id)

    # ----------------------------------------------------------------- trigger
    @callback
    def _handle_trigger(self, event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        new = event.data["new_state"]
        if old is None or new is None or new.state in _INVALID:
            return
        domain = new.domain
        if domain in EDGE_DOMAINS:
            edge = self.opt(CONF_TRIGGER_EDGE)
            hit = (
                (edge in (EDGE_ON, EDGE_BOTH) and old.state == STATE_OFF and new.state == STATE_ON)
                or (
                    edge in (EDGE_OFF, EDGE_BOTH)
                    and old.state == STATE_ON
                    and new.state == STATE_OFF
                )
            )
        else:
            # event / button / input_button: state is the timestamp of the last press
            hit = new.state != old.state
            allowed = [t.strip() for t in self.opt(CONF_EVENT_TYPES) or [] if t.strip()]
            if hit and domain == "event" and allowed:
                hit = new.attributes.get("event_type") in allowed
        if not hit:
            return

        now = dt_util.utcnow()
        debounce = float(self.opt(CONF_DEBOUNCE))
        if self._last_trigger and (now - self._last_trigger).total_seconds() < debounce:
            return
        self._last_trigger = now
        self.hass.async_create_task(
            self.async_ring(source="trigger", who=new.name, entity=new.entity_id)
        )

    @callback
    def _handle_watched(self, event: Event[EventStateChangedData]) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE, self.entry.entry_id)

    # -------------------------------------------------------------------- ring
    async def async_ring(
        self,
        source: str = "trigger",
        context: Context | None = None,
        who: str | None = None,
        entity: str | None = None,
    ) -> None:
        """Handle a ring: inform frontends, gong, push."""
        now = dt_util.utcnow()
        who = who or await self._user_name(context)
        st = self.state
        st.ring_id += 1
        st.ring_count += 1
        st.last_ring = now
        st.history.appendleft(RingRecord(now, source, who, entity))
        # Every ring gets the full live window, even right after the door was opened.
        st.view_until = now + timedelta(seconds=int(self.opt(CONF_LIVE_SECONDS)))

        self.hass.bus.async_fire(
            EVENT_RING,
            {
                "entry_id": self.entry.entry_id,
                "name": self.name,
                "source": source,
                "user": who,
                "entity_id": entity,
            },
        )
        async_dispatcher_send(self.hass, SIGNAL_RING, self.entry.entry_id, self.ring_payload())
        async_dispatcher_send(self.hass, SIGNAL_UPDATE, self.entry.entry_id)

        if gong := self.opt(CONF_GONG):
            self.hass.async_create_task(self._run_gong(gong))
        self.hass.async_create_task(self.notifier.async_ring(st.ring_id))

    def async_dismiss(self, ring_time: str, context: Context | None = None) -> bool:
        """Someone closed the live view of a ring: close it on every device.

        Only the ring that is still the latest one can be dismissed, so an old
        close never hides a newer ring.
        """
        st = self.state
        if not st.last_ring or st.last_ring.isoformat() != ring_time:
            return False
        st.view_until = dt_util.utcnow()
        async_dispatcher_send(self.hass, SIGNAL_DISMISS, self.entry.entry_id, ring_time)
        self.hass.bus.async_fire(
            EVENT_DISMISSED, {"entry_id": self.entry.entry_id, "name": self.name}, context=context
        )
        return True

    def ring_payload(self) -> dict[str, Any]:
        st = self.state
        return {
            "ring_id": st.ring_id,
            "time": st.last_ring.isoformat() if st.last_ring else None,
            "view_until": st.view_until.isoformat() if st.view_until else None,
        }

    async def _run_gong(self, entity_id: str) -> None:
        domain = entity_id.split(".")[0]
        try:
            if domain in PULSE_DOMAINS:
                await self._turn_on(entity_id)
                self._pending_off.add(entity_id)
                await self._save()
                if self._gong_cancel:
                    self._gong_cancel()
                self._gong_cancel = self._later(
                    float(self.opt(CONF_GONG_SECONDS)), lambda: self._end_gong(entity_id)
                )
            else:
                await self._activate(entity_id, None)
        except HomeAssistantError as err:
            _LOGGER.error("Portier: gong %s failed: %s", entity_id, err)

    async def _end_gong(self, entity_id: str) -> None:
        self._gong_cancel = None
        await self._turn_off(entity_id)

    # -------------------------------------------------------------------- open
    async def async_open(
        self, context: Context | None = None, via: str = "app", who: str | None = None
    ) -> None:
        """Open the door with whatever kind of opener is configured."""
        entity_id = self.opener
        if not entity_id:
            raise HomeAssistantError(f"{self.name}: kein Türöffner eingerichtet")
        async with self._lock:
            domain = entity_id.split(".")[0]
            if domain in PULSE_DOMAINS and self.opt(CONF_PULSE_MODE) != PULSE_DEVICE:
                if self._pulse_cancel:
                    self._pulse_cancel()
                    self._pulse_cancel = None
                # Remember the obligation before switching on, so a crash cannot
                # leave the strike energised.
                self._pending_off.add(entity_id)
                await self._save()
                try:
                    await self._turn_on(entity_id, context)
                finally:
                    self._pulse_cancel = self._later(
                        float(self.opt(CONF_PULSE_SECONDS)), lambda: self._end_pulse(entity_id)
                    )
            else:
                await self._activate(entity_id, context)

        now = dt_util.utcnow()
        who = who or await self._user_name(context)
        self.state.last_opened = now
        self.state.view_until = now + timedelta(seconds=int(self.opt(CONF_AFTER_OPEN_SECONDS)))
        self.state.opens.appendleft(OpenRecord(now, via, who))
        await self._save()
        self.hass.bus.async_fire(
            EVENT_OPENED,
            {"entry_id": self.entry.entry_id, "name": self.name, "via": via, "user": who},
            context=context,
        )
        async_dispatcher_send(self.hass, SIGNAL_UPDATE, self.entry.entry_id)
        self.notifier.async_door_opened(via, who)
        _LOGGER.info("Portier: %s geöffnet (%s, %s)", self.name, via, who or "ohne Benutzer")

    async def _user_name(self, context: Context | None) -> str | None:
        """Name of the Home Assistant user behind an action (None for automations)."""
        if not context or not context.user_id:
            return None
        user = await self.hass.auth.async_get_user(context.user_id)
        return user.name if user and user.name else None

    async def _end_pulse(self, entity_id: str) -> None:
        async with self._lock:
            self._pulse_cancel = None
            await self._turn_off(entity_id)

    async def _activate(self, entity_id: str, context: Context | None) -> None:
        """Non-pulse actions: press, open, unlock, run."""
        domain = entity_id.split(".")[0]
        if domain in ("button", "input_button"):
            service, data = "press", {}
        elif domain == "lock":
            state = self.hass.states.get(entity_id)
            features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if state else 0
            service = "open" if features & LockEntityFeature.OPEN else "unlock"
            data = {}
        elif domain == "cover":
            service, data = "open_cover", {}
        else:  # script, switch in device mode, siren, …
            service, data = "turn_on", {}
        await self._call(domain, service, entity_id, data, context)

    # ----------------------------------------------------------------- helpers
    def _later(self, delay: float, factory: Callable[[], Coroutine[Any, Any, None]]) -> CALLBACK_TYPE:
        """Run a coroutine after a delay, on the event loop."""

        @callback
        def _fire(_now: datetime) -> None:
            self.hass.async_create_task(factory())

        return async_call_later(self.hass, delay, _fire)

    async def _turn_on(self, entity_id: str, context: Context | None = None) -> None:
        await self._call(entity_id.split(".")[0], "turn_on", entity_id, {}, context)

    async def _turn_off(self, entity_id: str, attempt: int = 1) -> None:
        try:
            await self._call(entity_id.split(".")[0], "turn_off", entity_id, {}, None)
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.error(
                "Portier: %s konnte nicht ausgeschaltet werden (Versuch %s): %s",
                entity_id,
                attempt,
                err,
            )
            if attempt == 1:
                persistent_notification.async_create(
                    self.hass,
                    f"**{entity_id}** konnte nicht ausgeschaltet werden. "
                    "Portier versucht es weiter, bitte das Relais prüfen.",
                    title="Portier: Relais prüfen",
                    notification_id=f"{DOMAIN}_{self.entry.entry_id}_off",
                )
            # The obligation stays stored; retry with growing delay (max 5 min).
            if attempt < 20:
                self._later(min(5 * attempt, 300), lambda: self._turn_off(entity_id, attempt + 1))
            return
        if attempt > 1:
            persistent_notification.async_dismiss(
                self.hass, f"{DOMAIN}_{self.entry.entry_id}_off"
            )
        self._pending_off.discard(entity_id)
        await self._save()

    async def _call(
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: dict[str, Any],
        context: Context | None,
    ) -> None:
        async with asyncio.timeout(_SWITCH_TIMEOUT):
            await self.hass.services.async_call(
                domain,
                service,
                {ATTR_ENTITY_ID: entity_id, **data},
                blocking=True,
                context=context,
            )

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "pending_off": sorted(self._pending_off),
                "opens": [
                    {"time": r.time.isoformat(), "via": r.via, "who": r.who}
                    for r in self.state.opens
                ],
            }
        )

    # ---------------------------------------------------------------- snapshot
    def can_open(self, user_is_admin: bool) -> bool:
        return bool(self.opener) and (user_is_admin or not self.opt(CONF_ADMIN_ONLY))

    def snapshot(self, user_is_admin: bool) -> dict[str, Any]:
        """Everything a frontend needs to render this door."""
        st = self.state
        return {
            "entry_id": self.entry.entry_id,
            "name": self.name,
            "camera": self.camera,
            "opener": self.opener,
            "contact": self.contact,
            "can_open": self.can_open(user_is_admin),
            "live_seconds": int(self.opt(CONF_LIVE_SECONDS)),
            "after_open_seconds": int(self.opt(CONF_AFTER_OPEN_SECONDS)),
            "sound": self.opt(CONF_SOUND),
            "volume": int(self.opt(CONF_VOLUME)),
            "talk": bool(self.opt(CONF_TALK)),
            "ring_count": st.ring_count,
            "ring_id": st.ring_id,
            "last_ring": st.last_ring.isoformat() if st.last_ring else None,
            "last_opened": st.last_opened.isoformat() if st.last_opened else None,
            "view_until": st.view_until.isoformat() if st.view_until else None,
            "history": [
                {"time": r.time.isoformat(), "source": r.source, "who": r.who, "entity": r.entity}
                for r in st.history
            ],
            "opens": [
                {"time": r.time.isoformat(), "via": r.via, "who": r.who} for r in st.opens
            ],
        }


def door_for(hass: HomeAssistant, entry_id: str) -> PortierDoor:
    """Return the runtime door for an entry id."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        raise HomeAssistantError(f"Unbekannte Portier-Tür: {entry_id}")
    door = getattr(entry, "runtime_data", None)
    if not isinstance(door, PortierDoor):
        raise HomeAssistantError(f"Portier-Tür {entry_id} ist nicht geladen")
    return door


def all_doors(hass: HomeAssistant) -> list[PortierDoor]:
    """Return all loaded doors."""
    return [
        e.runtime_data
        for e in hass.config_entries.async_entries(DOMAIN)
        if isinstance(getattr(e, "runtime_data", None), PortierDoor)
    ]

