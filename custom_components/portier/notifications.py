"""Push notifications: Companion App, Telegram and any other notify service."""

from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    HomeAssistant,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTION_WINDOW,
    CONF_NOTIFY,
    CONF_NOTIFY_COOLDOWN,
    CONF_NOTIFY_OPEN,
    CONF_TELEGRAM_TARGETS,
    NOTIFY_ALL_MOBILE,
    PANEL_URL,
)

if TYPE_CHECKING:
    from .manager import PortierDoor

_LOGGER = logging.getLogger(__name__)

MOBILE_ACTION_EVENT = "mobile_app_notification_action"
TELEGRAM_CALLBACK_EVENT = "telegram_callback"
ACTION_PREFIX = "PORTIER_OPEN_"
TELEGRAM_COMMAND = "/portier_open"

VIA_TEXT = {
    "app": "über Home Assistant",
    "ha_app": "über die Home-Assistant-App",
    "browser": "über den Browser",
    "service": "über Home Assistant",
    "button": "über Home Assistant",
    "push": "über die App-Benachrichtigung",
    "nfc": "per NFC an der Tür",
}


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
        # Telegram messages whose "open" button is still live: (chat_id, message_id,
        # is_photo, caption) -> cancel of the expiry timer.
        self._tg_live: dict[tuple[int, int, bool, str], CALLBACK_TYPE] = {}

    # ------------------------------------------------------------------ listen
    def async_listen(self) -> list[CALLBACK_TYPE]:
        return [
            self.hass.bus.async_listen(MOBILE_ACTION_EVENT, self._on_mobile_action),
            self.hass.bus.async_listen(TELEGRAM_CALLBACK_EVENT, self._on_telegram_callback),
            self._forget_telegram,
        ]

    @callback
    def _forget_telegram(self) -> None:
        for cancel in self._tg_live.values():
            cancel()
        self._tg_live.clear()

    def _new_token(self) -> str:
        now = time.monotonic()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        token = secrets.token_hex(6)
        self._tokens[token] = now + float(self.door.opt(CONF_ACTION_WINDOW))
        return token

    def _consume(self, token: str) -> bool:
        """A token opens the door once, and only within the action window."""
        expiry = self._tokens.pop(token, None)
        return expiry is not None and expiry > time.monotonic()

    @property
    def _open_allowed(self) -> bool:
        return bool(self.door.opener) and bool(self.door.opt(CONF_NOTIFY_OPEN))

    def _services(self) -> list[str]:
        """Chosen notify services; "all phones" means every mobile_app service right now."""
        chosen = [s.removeprefix("notify.") for s in self.door.opt(CONF_NOTIFY) or []]
        result: list[str] = []
        for service in chosen:
            if service == NOTIFY_ALL_MOBILE:
                result.extend(
                    sorted(
                        s
                        for s in self.hass.services.async_services_for_domain("notify")
                        if s.startswith("mobile_app_")
                    )
                )
            else:
                result.append(service)
        return list(dict.fromkeys(result))  # each phone once, even if also ticked singly

    # -------------------------------------------------------------------- send
    async def async_ring(self, ring_id: int) -> None:
        services = self._services()
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

    async def async_info(self, title: str, message: str) -> None:
        """A plain note (no picture, no button) to the door's push and Telegram receivers."""
        for service in self._services():
            data: dict[str, Any] = {"title": title, "message": message}
            if service.startswith("mobile_app_"):
                data["data"] = {"tag": f"portier_info_{self.door.entry.entry_id}", "group": "portier"}
            try:
                await self._call("notify", service, data)
            except (HomeAssistantError, ValueError, TimeoutError) as err:
                _LOGGER.warning("Portier: Hinweis über %s fehlgeschlagen: %s", service, err)
        targets = parse_telegram_targets(self.door.opt(CONF_TELEGRAM_TARGETS))
        if targets and self.hass.services.has_service("telegram_bot", "send_message"):
            try:
                await self._call(
                    "telegram_bot",
                    "send_message",
                    {"chat_id": targets, "message": f"{title}\n{message}"},
                )
            except (HomeAssistantError, ValueError, TimeoutError) as err:
                _LOGGER.warning("Portier: Telegram-Hinweis fehlgeschlagen: %s", err)

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
                response = await self._call_tg(
                    "send_photo",
                    {
                        "chat_id": targets,
                        "url": photo_url,
                        "verify_ssl": False,
                        "caption": caption,
                        **keyboard,
                    },
                )
            except (HomeAssistantError, ValueError, TimeoutError) as err:
                _LOGGER.warning("Portier: Telegram-Foto fehlgeschlagen, sende Text: %s", err)
            else:
                if token:
                    self._track_telegram(response, True, caption)
                return
        try:
            response = await self._call_tg(
                "send_message", {"chat_id": targets, "message": caption, **keyboard}
            )
        except (HomeAssistantError, ValueError, TimeoutError) as err:
            _LOGGER.error("Portier: Telegram fehlgeschlagen: %s", err)
            return
        if token:
            self._track_telegram(response, False, caption)

    async def _call_tg(self, service: str, data: dict[str, Any]) -> Any:
        """Call a telegram_bot send service, asking for the message ids if possible."""
        wants = (
            self.hass.services.supports_response("telegram_bot", service)
            != SupportsResponse.NONE
        )
        return await self.hass.services.async_call(
            "telegram_bot", service, data, blocking=True, return_response=wants
        )

    def _track_telegram(self, response: Any, is_photo: bool, caption: str) -> None:
        """Remember sent messages so their button can be removed later."""
        chats = response.get("chats") if isinstance(response, dict) else None
        window = float(self.door.opt(CONF_ACTION_WINDOW))
        for item in chats or []:
            try:
                key = (int(item["chat_id"]), int(item["message_id"]), is_photo, caption)
            except (KeyError, TypeError, ValueError):
                continue

            @callback
            def _expired(_now: Any, key: tuple[int, int, bool, str] = key) -> None:
                if self._tg_live.pop(key, None) is None:
                    return
                self.hass.async_create_task(
                    self._edit_telegram(key, "⏱ Öffnen nicht mehr möglich")
                )

            self._tg_live[key] = async_call_later(self.hass, window, _expired)

    @callback
    def async_door_opened(self, via: str, who: str | None = None) -> None:
        """The door was opened somehow: say so in every Telegram message still live."""
        if not self._tg_live:
            return
        how = "über Telegram" if via == "telegram" else VIA_TEXT.get(via, f"über {via}")
        if who:
            how = f"von {who} {how}"
        note = f"✅ Tür geöffnet · {dt_util.now().strftime('%H:%M:%S')} · {how}"
        live, self._tg_live = self._tg_live, {}
        for key, cancel in live.items():
            cancel()
            self.hass.async_create_task(self._edit_telegram(key, note))

    async def _edit_telegram(self, key: tuple[int, int, bool, str], note: str) -> None:
        chat_id, message_id, is_photo, caption = key
        text = f"{caption}\n{note}"
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "inline_keyboard": [],
        }
        if is_photo:
            service, data["caption"] = "edit_caption", text
        else:
            service, data["message"] = "edit_message", text
        try:
            await self._call("telegram_bot", service, data)
        except (HomeAssistantError, ValueError, TimeoutError) as err:
            _LOGGER.debug("Portier: Telegram-Nachricht nicht geändert: %s", err)

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
        who = " ".join(
            p for p in (event.data.get("from_first"), event.data.get("from_last")) if p
        ) or None
        self.hass.async_create_task(self._open(None, "telegram", callback_id, who))

    async def _open(
        self,
        context: Context | None,
        via: str,
        callback_id: str | None = None,
        who: str | None = None,
    ) -> None:
        try:
            await self.door.async_open(context=context, via=via, who=who)
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
