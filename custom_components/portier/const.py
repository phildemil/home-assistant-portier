"""Constants for Portier."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "portier"
PLATFORMS: Final = ["button", "event", "sensor"]

# Devices
CONF_NAME: Final = "name"
CONF_TRIGGER: Final = "trigger_entity"
CONF_CAMERA: Final = "camera_entity"
CONF_OPENER: Final = "opener_entity"
CONF_CONTACT: Final = "contact_entity"
CONF_GONG: Final = "gong_entity"

# Trigger details
CONF_TRIGGER_EDGE: Final = "trigger_edge"
CONF_EVENT_TYPES: Final = "event_types"
CONF_DEBOUNCE: Final = "debounce_seconds"

# Opener / gong
CONF_PULSE_MODE: Final = "pulse_mode"
CONF_PULSE_SECONDS: Final = "pulse_seconds"
CONF_GONG_SECONDS: Final = "gong_seconds"
CONF_ADMIN_ONLY: Final = "open_admin_only"

# Live view
CONF_LIVE_SECONDS: Final = "live_seconds"
CONF_AFTER_OPEN_SECONDS: Final = "after_open_seconds"
CONF_SOUND: Final = "sound"
CONF_VOLUME: Final = "volume"
CONF_TALK: Final = "talk"

# Notifications
CONF_NOTIFY: Final = "notify_services"
CONF_TELEGRAM_TARGETS: Final = "telegram_targets"
CONF_NOTIFY_OPEN: Final = "notify_allow_open"
CONF_NOTIFY_COOLDOWN: Final = "notify_cooldown"
CONF_ACTION_WINDOW: Final = "action_window_seconds"
OLD_ACTION_WINDOW_MINUTES: Final = "action_window_minutes"
ACTION_WINDOW_MAX: Final = 120

# Form sections
SECTION_TRIGGER: Final = "trigger_options"
SECTION_OPENER: Final = "opener_options"
SECTION_NOTIFY: Final = "notifications"
SECTION_VIEW: Final = "live_view"

EDGE_ON: Final = "on"
EDGE_OFF: Final = "off"
EDGE_BOTH: Final = "both"

PULSE_PORTIER: Final = "portier"
PULSE_DEVICE: Final = "device"

SOUND_DINGDONG: Final = "dingdong"
SOUND_SOFT: Final = "soft"
SOUND_TRIPLE: Final = "triple"
SOUND_OFF: Final = "off"

DEFAULTS: Final = {
    CONF_TRIGGER_EDGE: EDGE_ON,
    CONF_EVENT_TYPES: [],
    CONF_DEBOUNCE: 2.0,
    CONF_PULSE_MODE: PULSE_PORTIER,
    CONF_PULSE_SECONDS: 3.0,
    CONF_GONG_SECONDS: 1.0,
    CONF_ADMIN_ONLY: False,
    CONF_LIVE_SECONDS: 60,
    CONF_AFTER_OPEN_SECONDS: 20,
    CONF_SOUND: SOUND_DINGDONG,
    CONF_VOLUME: 70,
    CONF_TALK: False,
    CONF_NOTIFY: [],
    CONF_TELEGRAM_TARGETS: "",
    CONF_NOTIFY_OPEN: True,
    CONF_NOTIFY_COOLDOWN: 30,
    CONF_ACTION_WINDOW: 120,
}

# Entity domains
TRIGGER_DOMAINS: Final = [
    "binary_sensor",
    "event",
    "input_boolean",
    "input_button",
    "button",
    "switch",
]
EDGE_DOMAINS: Final = {"binary_sensor", "input_boolean", "switch"}
OPENER_DOMAINS: Final = [
    "switch",
    "light",
    "input_boolean",
    "button",
    "input_button",
    "lock",
    "script",
    "cover",
]
PULSE_DOMAINS: Final = {"switch", "light", "input_boolean"}
GONG_DOMAINS: Final = [
    "switch",
    "light",
    "input_boolean",
    "button",
    "input_button",
    "script",
    "siren",
]
CONTACT_DOMAINS: Final = ["binary_sensor", "input_boolean", "lock", "cover"]

EVENT_RING: Final = f"{DOMAIN}_ring"
EVENT_OPENED: Final = f"{DOMAIN}_opened"
SIGNAL_RING: Final = f"{DOMAIN}_ring_signal"
SIGNAL_UPDATE: Final = f"{DOMAIN}_update_signal"
SIGNAL_DISMISS: Final = f"{DOMAIN}_dismiss_signal"
EVENT_DISMISSED: Final = f"{DOMAIN}_dismissed"

HISTORY_SIZE: Final = 20
PANEL_URL: Final = "portier"
STATIC_URL: Final = "/portier_static"
