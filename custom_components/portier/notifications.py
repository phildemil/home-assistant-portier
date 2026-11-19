"""Push notifications: Companion App, Telegram and any other notify service."""

from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTION_WINDOW,
    CONF_NOTIFY,
    CONF_NOTIFY_COOLDOWN,
    CONF_NOTIFY_OPEN,
    CONF_TELEGRAM_TARGETS,
    PANEL_URL,
)

if TYPE_CHECKING:
    from .manager import PortierDoor

_LOGGER = logging.getLogger(__name__)

MOBILE_ACTION_EVENT = "mobile_app_notification_action"
TELEGRAM_CALLBACK_EVENT = "telegram_callback"
ACTION_PREFIX = "PORTIER_OPEN_"
TELEGRAM_COMMAND = "/portier_open"


def parse_telegram_targets(raw: str | None) -> list[int]:
    """Turn '123, -456' into [123, -456]; ignore garbage."""
    result: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            result.append(int(part))
    return result


class Notifier:
    """Sends ring notifications and handles their "open door" buttons."""

    def __init__(self, hass: HomeAssistant, door: PortierDoor) -> None:
        self.hass = hass
        self.door = door
        self._tokens: dict[str, float] = {}
        self._last_sent = 0.0

    # ------------------------------------------------------------------ listen
    def async_listen(self) -> list[CALLBACK_TYPE]:
        return [
            self.hass.bus.async_listen(MOBILE_ACTION_EVENT, self._on_mobile_action),
            self.hass.bus.async_listen(TELEGRAM_CALLBACK_EVENT, self._on_telegram_callback),
        ]

    def _new_token(self) -> str:
        now = time.monotonic()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        token = secrets.token_hex(6)
        self._tokens[token] = now + 60 * float(self.door.opt(CONF_ACTION_WINDOW))
        return token

    def _consume(self, token: str) -> bool:
        """A token opens the door once, and only within the action window."""
        expiry = self._tokens.pop(token, None)
        return expiry is not None and expiry > time.monotonic()

    @property
    def _open_allowed(self) -> bool:
        return bool(self.door.opener) and bool(self.door.opt(CONF_NOTIFY_OPEN))

    # -------------------------------------------------------------------- send
    async def async_ring(self, ring_id: int) -> None:
        services = [s.removeprefix("notify.") for s in self.door.opt(CONF_NOTIFY) or []]
        targets = parse_telegram_targets(self.door.opt(CONF_TELEGRAM_TARGETS))
        if not services and not targets:
            return
        now = time.monotonic()
        cooldown = float(self.door.opt(CONF_NOTIFY_COOLDOWN))
        if self._last_sent and now - self._last_sent < cooldown:
            _LOGGER.debug("Portier: Push unterdrückt (Abstand %ss)", cooldown)
            return
        self._last_sent = now

        token = self._new_token() if self._open_allowed else None
        title = f"🔔 {self.door.name}"
        message = f"Es klingelt · {dt_util.now().strftime('%H:%M:%S')}"

        for service in services:
            try:
                if service.startswith("mobile_app_"):
                    await self._send_mobile(service, title, message, token)
                else:
                    await self._call("notify", service, {"title": title, "message": message})
            except (HomeAssistantError, ValueError, TimeoutError) as err:
                _LOGGER.error("Portier: Benachrichtigung über %s fehlgeschlagen: %s", service, err)

        if targets:
            await self._send_telegram(targets, f"{title}\n{message}", token)

    async def _send_mobile(
        self, service: str, title: str, message: str, token: str | None
    ) -> None:
        entry_id = self.door.entry.entry_id
        # Tapping the notification opens the live picture with the opener inside it.
        live_url = f"/{PANEL_URL}?show={entry_id}"
        data: dict[str, Any] = {
            "tag": f"portier_{entry_id}",
            "group": "portier",
            # Android
            "channel": "Türklingel",
            "importance": "high",
            "priority": "high",
            "ttl": 0,
            "clickAction": live_url,
            # iOS
            "url": live_url,
            "push": {"interruption-level": "time-sensitive"},
        }
        if camera := self.door.camera:
            # The app downloads this itself with its own login.
            data["image"] = f"/api/camera_proxy/{camera}"
        actions: list[dict[str, Any]] = []
        if token:
            actions.append(
                {
                    "action": f"{ACTION_PREFIX}{token}",
                    "title": "🔓 Tür öffnen",
                    "authenticationRequired": True,
                    "destructive": True,
                }
            )
        if actions:
            data["actions"] = actions
        await self._call("notify", service, {"title": title, "message": message, "data": data})

    async def _send_telegram(self, targets: list[int], caption: str, token: str | None) -> None:
        if not self.hass.services.has_service("telegram_bot", "send_message"):
            _LOGGER.warning("Portier: Telegram-Ziele eingetragen, aber kein Telegram-Bot in HA")
            return
        keyboard = (
            {"inline_keyboard": [[["🔓 Tür öffnen", f"{TELEGRAM_COMMAND} {token}"]]]}
            if token
            else {}
        )
        photo_url = self._camera_url()
        if photo_url:
            try:
                await self._call(
                    "telegram_bot",
                    "send_photo",
                    {
                        "chat_id": targets,
                        "url": photo_url,
                        "verify_ssl": False,
                        "caption": caption,
                        **keyboard,
                    },
                )
                return
            except (HomeAssistantError, ValueError, TimeoutError) as err:
                _LOGGER.warning("Portier: Telegram-Foto fehlgeschlagen, sende Text: %s", err)
        try:
            await self._call(
                "telegram_bot", "send_message", {"chat_id": targets, "message": caption, **keyboard}
            )
        except (HomeAssistantError, ValueError, TimeoutError) as err:
            _LOGGER.error("Portier: Telegram fehlgeschlagen: %s", err)

    def _camera_url(self) -> str | None:
        """Short-lived, HA-internal snapshot URL for Telegram to download."""
        camera = self.door.camera
        state = self.hass.states.get(camera) if camera else None
        token = state.attributes.get("access_token") if state else None
        if not token:
            return None
        try:
            base = get_url(self.hass, allow_external=False, allow_cloud=False)
        except NoURLAvailableError:
            base = f"http://127.0.0.1:{self.hass.http.server_port}"
        return f"{base}/api/camera_proxy/{camera}?token={token}"

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        await self.hass.services.async_call(domain, service, data, blocking=True)

    # ----------------------------------------------------------------- actions
    @callback
    def _on_mobile_action(self, event: Event) -> None:
        action = str(event.data.get("action", ""))
        if not action.startswith(ACTION_PREFIX):
            return
        token = action.removeprefix(ACTION_PREFIX)
        if token not in self._tokens:
            return  # belongs to another door
        if not self._consume(token):
            _LOGGER.warning("Portier: abgelaufener Öffnen-Knopf aus Push ignoriert")
            return
        self.hass.async_create_task(
            self._open(Context(user_id=event.context.user_id), "push")
        )

    @callback
    def _on_telegram_callback(self, event: Event) -> None:
        data = str(event.data.get("data", ""))
        if not data.startswith(TELEGRAM_COMMAND + " "):
            return
        token = data.removeprefix(TELEGRAM_COMMAND + " ").strip()
        if token not in self._tokens:
            return
        chat_id = event.data.get("chat_id")
        callback_id = event.data.get("id")
        allowed = parse_telegram_targets(self.door.opt(CONF_TELEGRAM_TARGETS))
        if chat_id not in allowed:
            _LOGGER.warning("Portier: Telegram-Öffnen aus fremdem Chat %s abgelehnt", chat_id)
            return
        if not self._consume(token):
            self.hass.async_create_task(self._answer(callback_id, "⏱ Zu spät – Knopf abgelaufen"))
            return
        who = event.data.get("from_first") or "Telegram"
        self.hass.async_create_task(self._open(None, f"telegram ({who})", callback_id))

    async def _open(
        self, context: Context | None, via: str, callback_id: str | None = None
    ) -> None:
        try:
            await self.door.async_open(context=context, via=via)
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.error("Portier: Öffnen über %s fehlgeschlagen: %s", via, err)
            if callback_id:
                await self._answer(callback_id, "⚠️ Öffnen fehlgeschlagen")
            return
        if callback_id:
            await self._answer(callback_id, "✅ Tür geöffnet")

    async def _answer(self, callback_id: str | None, text: str) -> None:
        if not callback_id:
            return
        try:
            await self._call(
                "telegram_bot",
                "answer_callback_query",
                {"callback_query_id": callback_id, "message": text},
            )
        except (HomeAssistantError, ValueError, TimeoutError) as err:
            _LOGGER.debug("Portier: Telegram-Antwort fehlgeschlagen: %s", err)
