"""Setup and options forms."""

from __future__ import annotations

from typing import Any

import probatio as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow, OptionsFlowWithReload
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    NOTIFY_ALL_MOBILE,
    CONF_ACTION_WINDOW,
    CONF_ADMIN_ONLY,
    CONF_TALK,
    CONF_AFTER_OPEN_SECONDS,
    CONF_CAMERA,
    CONF_CONTACT,
    CONF_DEBOUNCE,
    CONF_EVENT_TYPES,
    CONF_GONG,
    CONF_GONG_SECONDS,
    CONF_LIVE_SECONDS,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_COOLDOWN,
    CONF_NOTIFY_OPEN,
    CONF_OPENER,
    CONF_PULSE_MODE,
    CONF_PULSE_SECONDS,
    CONF_SOUND,
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
    PULSE_DEVICE,
    PULSE_PORTIER,
    SECTION_NOTIFY,
    SECTION_OPENER,
    SECTION_TRIGGER,
    SECTION_VIEW,
    SOUND_DINGDONG,
    SOUND_OFF,
    SOUND_SOFT,
    SOUND_TRIPLE,
    TRIGGER_DOMAINS,
)
from .frontend import loader_configured
from .notifications import parse_telegram_targets

DEVICE_KEYS = (CONF_TRIGGER, CONF_CAMERA, CONF_OPENER, CONF_CONTACT, CONF_GONG)
SECTIONS = {
    SECTION_TRIGGER: (CONF_TRIGGER_EDGE, CONF_EVENT_TYPES, CONF_DEBOUNCE),
    SECTION_OPENER: (CONF_PULSE_MODE, CONF_PULSE_SECONDS, CONF_GONG_SECONDS, CONF_ADMIN_ONLY),
    SECTION_NOTIFY: (
        CONF_NOTIFY,
        CONF_TELEGRAM_TARGETS,
        CONF_NOTIFY_OPEN,
        CONF_NOTIFY_COOLDOWN,
        CONF_ACTION_WINDOW,
    ),
    SECTION_VIEW: (CONF_LIVE_SECONDS, CONF_AFTER_OPEN_SECONDS, CONF_SOUND, CONF_VOLUME, CONF_TALK),
}


def _entity(domains: list[str]) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=domains))


def _num(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


def _select(options: list[str], key: str, *, multiple: bool = False, custom: bool = False):
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            translation_key=key,
            multiple=multiple,
            custom_value=custom,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _opt(key: str, values: dict[str, Any]) -> vol.Optional:
    """Optional field pre-filled with the current or default value."""
    value = values.get(key, DEFAULTS.get(key))
    if value in (None, "", []):
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": value})


def _notify_services(hass: HomeAssistant) -> list[str]:
    # "notify" alone sends to every phone at once and would only double the push.
    skip = {"send_message", "persistent_notification", "notify"}
    return sorted(s for s in hass.services.async_services_for_domain("notify") if s not in skip)


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


def _telegram_field(hass: HomeAssistant, values: dict[str, Any]) -> tuple[vol.Optional, Any]:
    """Tick the chats known to the Telegram bot; other IDs can still be typed in."""
    saved = [str(i) for i in parse_telegram_targets(values.get(CONF_TELEGRAM_TARGETS))]
    known = {str(c["id"]): c["name"] for c in telegram_chats(hass)}
    options = [
        SelectOptionDict(value=cid, label=f"{name} ({cid})") for cid, name in known.items()
    ] + [SelectOptionDict(value=cid, label=cid) for cid in saved if cid not in known]
    key = (
        vol.Optional(CONF_TELEGRAM_TARGETS, description={"suggested_value": saved})
        if saved
        else vol.Optional(CONF_TELEGRAM_TARGETS)
    )
    selector = SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=True,
            custom_value=True,
            mode=SelectSelectorMode.LIST if options else SelectSelectorMode.DROPDOWN,
        )
    )
    return key, selector


def _telegram_to_text(flat: dict[str, Any]) -> None:
    """The form gives a list; options keep the comma-separated text."""
    value = flat.get(CONF_TELEGRAM_TARGETS)
    if isinstance(value, list):
        flat[CONF_TELEGRAM_TARGETS] = ", ".join(
            str(i) for i in parse_telegram_targets(",".join(str(v) for v in value))
        )


def _device_fields(values: dict[str, Any], with_name: bool) -> dict:
    fields: dict = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=values.get(CONF_NAME, "Haustür"))] = TextSelector()
    fields[
        vol.Required(CONF_TRIGGER, description={"suggested_value": values.get(CONF_TRIGGER)})
    ] = _entity(TRIGGER_DOMAINS)
    fields[_opt(CONF_CAMERA, values)] = _entity(["camera"])
    fields[_opt(CONF_OPENER, values)] = _entity(OPENER_DOMAINS)
    fields[_opt(CONF_CONTACT, values)] = _entity(CONTACT_DOMAINS)
    fields[_opt(CONF_GONG, values)] = _entity(GONG_DOMAINS)
    return fields


def _section_fields(hass: HomeAssistant, values: dict[str, Any]) -> dict:
    trigger = {
        _opt(CONF_TRIGGER_EDGE, values): _select([EDGE_ON, EDGE_OFF, EDGE_BOTH], "edge"),
        _opt(CONF_EVENT_TYPES, values): _select([], "event_types", multiple=True, custom=True),
        _opt(CONF_DEBOUNCE, values): _num(0, 30, 0.1, "s"),
    }
    opener = {
        _opt(CONF_PULSE_MODE, values): _select([PULSE_PORTIER, PULSE_DEVICE], "pulse_mode"),
        _opt(CONF_PULSE_SECONDS, values): _num(0.1, 60, 0.1, "s"),
        _opt(CONF_GONG_SECONDS, values): _num(0.1, 30, 0.1, "s"),
        _opt(CONF_ADMIN_ONLY, values): BooleanSelector(),
    }
    notify = {
        _opt(CONF_NOTIFY, values): _select(
            [NOTIFY_ALL_MOBILE, *_notify_services(hass)], "notify", multiple=True, custom=True
        ),
        **dict([_telegram_field(hass, values)]),
        _opt(CONF_NOTIFY_OPEN, values): BooleanSelector(),
        _opt(CONF_NOTIFY_COOLDOWN, values): _num(0, 600, 1, "s"),
        _opt(CONF_ACTION_WINDOW, values): _num(1, 120, 1, "s"),
    }
    view = {
        _opt(CONF_LIVE_SECONDS, values): _num(5, 3600, 1, "s"),
        _opt(CONF_AFTER_OPEN_SECONDS, values): _num(1, 3600, 1, "s"),
        _opt(CONF_SOUND, values): _select(
            [SOUND_DINGDONG, SOUND_SOFT, SOUND_TRIPLE, SOUND_OFF], "sound"
        ),
        _opt(CONF_VOLUME, values): _num(0, 100, 5, "%"),
        _opt(CONF_TALK, values): BooleanSelector(),
    }
    return {
        vol.Required(SECTION_NOTIFY): section(vol.Schema(notify), {"collapsed": False}),
        vol.Required(SECTION_OPENER): section(vol.Schema(opener), {"collapsed": True}),
        vol.Required(SECTION_TRIGGER): section(vol.Schema(trigger), {"collapsed": True}),
        vol.Required(SECTION_VIEW): section(vol.Schema(view), {"collapsed": True}),
    }


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Merge section dicts into one flat options dict, dropping empty device fields."""
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in SECTIONS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    for key in DEVICE_KEYS:
        if not flat.get(key):
            flat[key] = None
    return flat


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
        vol.Required(CONF_TRIGGER): _domain_entity(TRIGGER_DOMAINS),
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
    """Create a door."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Door created from the side panel (already validated)."""
        return self.async_create_entry(
            title=import_data[CONF_NAME], data={}, options=import_data
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = _flatten(user_input)
            errors = _validate(self.hass, flat, None)
            if not errors:
                self._data = flat
                return await self.async_step_notify()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(_device_fields(user_input or {}, with_name=True)),
            errors=errors,
        )

    async def async_step_notify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            user_input = dict(user_input)
            _telegram_to_text(user_input)
            options = {**DEFAULTS, **self._data, **user_input}
            return self.async_create_entry(
                title=self._data[CONF_NAME],
                data={},
                options=options,
                # Usable right away; say plainly what is still missing for the live view.
                description=None if loader_configured(self.hass) else "loader_pending",
            )
        values = dict(DEFAULTS)
        fields = {
            _opt(CONF_NOTIFY, values): _select(
                [NOTIFY_ALL_MOBILE, *_notify_services(self.hass)],
                "notify",
                multiple=True,
                custom=True,
            ),
            **dict([_telegram_field(self.hass, values)]),
            _opt(CONF_NOTIFY_OPEN, values): BooleanSelector(),
        }
        return self.async_show_form(step_id="notify", data_schema=vol.Schema(fields))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PortierOptionsFlow()


class PortierOptionsFlow(OptionsFlowWithReload):
    """Change everything later."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**DEFAULTS, **self.config_entry.options}
        if user_input is not None:
            flat = _flatten(user_input)
            for key in (CONF_NOTIFY, CONF_EVENT_TYPES):
                flat.setdefault(key, [])
            flat.setdefault(CONF_TELEGRAM_TARGETS, "")
            _telegram_to_text(flat)
            errors = _validate(self.hass, flat, self.config_entry.entry_id)
            if not errors:
                options = {**current, **flat}
                if options.get(CONF_NAME) and options[CONF_NAME] != self.config_entry.title:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, title=options[CONF_NAME]
                    )
                return self.async_create_entry(data=options)
            current = {**current, **flat}
        schema = {
            vol.Required(CONF_NAME, default=current.get(CONF_NAME) or self.config_entry.title): (
                TextSelector()
            ),
            **_device_fields(current, with_name=False),
            **_section_fields(self.hass, current),
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema), errors=errors)
