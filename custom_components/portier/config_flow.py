"""Setup and options flows.

Adding a door only asks for its name; everything else is set up in the
"Türklingel" side panel, so there is one place for all settings.
"""

from __future__ import annotations

from typing import Any

import probatio as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import TextSelector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTION_WINDOW,
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
    CONF_NFC_DEVICES,
    CONF_NFC_FROM,
    CONF_NFC_NOTIFY,
    CONF_NFC_TAGS,
    CONF_NFC_UNTIL,
    CONF_NOTIFY,
    CONF_NOTIFY_COOLDOWN,
    CONF_NOTIFY_OPEN,
    CONF_OPENER,
    CONF_PULSE_MODE,
    CONF_PULSE_SECONDS,
    CONF_SOUND,
    CONF_PRELOAD,
    CONF_TALK,
    CONF_TELEGRAM_TARGETS,
    CONF_TRIGGER,
    CONF_TRIGGER_EDGE,
    CONF_VOLUME,
    CONTACT_DOMAINS,
    DEFAULTS,
    DOMAIN,
    EDGE_BOTH,
    EDGE_OFF,
    EDGE_ON,
    GONG_DOMAINS,
    OPENER_DOMAINS,
    PANEL_URL,
    PULSE_DEVICE,
    PULSE_PORTIER,
    SOUND_DINGDONG,
    SOUND_OFF,
    SOUND_SOFT,
    SOUND_TRIPLE,
    TRIGGER_DOMAINS,
)

DEVICE_KEYS = (CONF_TRIGGER, CONF_CAMERA, CONF_OPENER, CONF_CONTACT, CONF_GONG)
# Link that opens the side panel's editor for a door ("new" = the door just added).
EDIT_URL = f"/{PANEL_URL}?edit={{}}"


def telegram_chats(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The allowed chats set up in the Telegram bot integration, by name."""
    chats: list[dict[str, Any]] = []
    seen: set[int] = set()
    for entry in hass.config_entries.async_entries("telegram_bot"):
        for sub in getattr(entry, "subentries", {}).values():
            if sub.subentry_type != "allowed_chat_ids":
                continue
            try:
                chat_id = int(sub.data.get("chat_id"))
            except (TypeError, ValueError):
                continue
            if chat_id in seen:
                continue
            seen.add(chat_id)
            chats.append({"id": chat_id, "name": sub.title or str(chat_id)})
    return chats


def _validate(
    hass: HomeAssistant, flat: dict[str, Any], own_entry_id: str | None
) -> dict[str, str]:
    errors: dict[str, str] = {}
    trigger, opener, gong = flat.get(CONF_TRIGGER), flat.get(CONF_OPENER), flat.get(CONF_GONG)
    if opener and opener in (trigger, gong):
        errors[CONF_OPENER] = "same_entity"
    if opener:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id != own_entry_id and entry.options.get(CONF_OPENER) == opener:
                errors[CONF_OPENER] = "opener_in_use"
    if flat.get(CONF_NFC_TAGS):
        if not opener:
            errors[CONF_NFC_TAGS] = "nfc_needs_opener"
        elif not flat.get(CONF_NFC_DEVICES):
            errors[CONF_NFC_DEVICES] = "nfc_needs_phone"
    return errors


def _domain_entity(domains: list[str]):
    def check(value: Any) -> str | None:
        if value in (None, ""):
            return None
        value = cv.entity_id(value)
        if value.split(".")[0] not in domains:
            raise vol.Invalid(f"{value}: falscher Typ")
        return value

    return check


def _time_or_none(value: Any) -> str | None:
    """'7:05' / '07:05:00' -> '07:05:00'; empty -> None."""
    if value in (None, ""):
        return None
    parsed = dt_util.parse_time(str(value).strip())
    if parsed is None:
        raise vol.Invalid("Uhrzeit wie 07:00 eingeben")
    return parsed.strftime("%H:%M:%S")


def _list_of_str(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()]


# Options as sent by the side panel. Unknown keys are dropped.
PANEL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): vol.All(str, vol.Strip, vol.Length(min=1, max=60)),
        vol.Optional(CONF_TRIGGER): _domain_entity(TRIGGER_DOMAINS),
        vol.Optional(CONF_CAMERA): _domain_entity(["camera"]),
        vol.Optional(CONF_OPENER): _domain_entity(OPENER_DOMAINS),
        vol.Optional(CONF_CONTACT): _domain_entity(CONTACT_DOMAINS),
        vol.Optional(CONF_GONG): _domain_entity(GONG_DOMAINS),
        vol.Optional(CONF_TRIGGER_EDGE): vol.In([EDGE_ON, EDGE_OFF, EDGE_BOTH]),
        vol.Optional(CONF_EVENT_TYPES): _list_of_str,
        vol.Optional(CONF_DEBOUNCE): vol.All(vol.Coerce(float), vol.Range(0, 30)),
        vol.Optional(CONF_PULSE_MODE): vol.In([PULSE_PORTIER, PULSE_DEVICE]),
        vol.Optional(CONF_PULSE_SECONDS): vol.All(vol.Coerce(float), vol.Range(0.1, 60)),
        vol.Optional(CONF_GONG_SECONDS): vol.All(vol.Coerce(float), vol.Range(0.1, 30)),
        vol.Optional(CONF_ADMIN_ONLY): bool,
        vol.Optional(CONF_NOTIFY): _list_of_str,
        vol.Optional(CONF_TELEGRAM_TARGETS): vol.Any(None, str),
        vol.Optional(CONF_NOTIFY_OPEN): bool,
        vol.Optional(CONF_NOTIFY_COOLDOWN): vol.All(vol.Coerce(float), vol.Range(0, 600)),
        vol.Optional(CONF_ACTION_WINDOW): vol.All(vol.Coerce(float), vol.Range(1, 120)),
        vol.Optional(CONF_LIVE_SECONDS): vol.All(vol.Coerce(int), vol.Range(5, 3600)),
        vol.Optional(CONF_AFTER_OPEN_SECONDS): vol.All(vol.Coerce(int), vol.Range(1, 3600)),
        vol.Optional(CONF_SOUND): vol.In([SOUND_DINGDONG, SOUND_SOFT, SOUND_TRIPLE, SOUND_OFF]),
        vol.Optional(CONF_VOLUME): vol.All(vol.Coerce(int), vol.Range(0, 100)),
        vol.Optional(CONF_TALK): bool,
        vol.Optional(CONF_PRELOAD): bool,
        vol.Optional(CONF_NFC_TAGS): _list_of_str,
        vol.Optional(CONF_NFC_DEVICES): _list_of_str,
        vol.Optional(CONF_NFC_FROM): _time_or_none,
        vol.Optional(CONF_NFC_UNTIL): _time_or_none,
        vol.Optional(CONF_NFC_NOTIFY): bool,
    },
    extra=vol.REMOVE_EXTRA,
)


def panel_options(
    hass: HomeAssistant, raw: dict[str, Any], own_entry_id: str | None, base: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate options coming from the side panel. Returns (options, errors)."""
    try:
        data = PANEL_SCHEMA(raw)
    except vol.MultipleInvalid as err:
        return {}, {str(e.path[0]) if e.path else "base": str(e.msg) for e in err.errors}
    options = {**DEFAULTS, **base, **data}
    for key in DEVICE_KEYS:
        options[key] = options.get(key) or None
    options[CONF_TELEGRAM_TARGETS] = options.get(CONF_TELEGRAM_TARGETS) or ""
    errors = _validate(hass, options, own_entry_id)
    return options, errors


class PortierConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a door: just its name, the rest happens in the side panel."""

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Door created from the side panel (already validated)."""
        return self.async_create_entry(
            title=import_data[CONF_NAME], data={}, options=import_data
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_NAME) or "").strip()[:60]
            if name:
                first = not self.hass.config_entries.async_entries(DOMAIN)
                options = {
                    **DEFAULTS,
                    **dict.fromkeys(DEVICE_KEYS),
                    CONF_NAME: name,
                    CONF_TELEGRAM_TARGETS: "",
                }
                return self.async_create_entry(
                    title=name,
                    data={},
                    options=options,
                    description="first_door" if first else "door_added",
                    description_placeholders={"url": EDIT_URL.format("new")},
                )
            errors[CONF_NAME] = "name_missing"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default="Haustür"): TextSelector()}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PortierOptionsFlow()


class PortierOptionsFlow(OptionsFlow):
    """"Configure" only points to the side panel, where all settings live."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(
            reason="use_panel",
            description_placeholders={"url": EDIT_URL.format(self.config_entry.entry_id)},
        )
