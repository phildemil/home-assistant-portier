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
# A lock that can only unlock (no "open"): lock it again after this many seconds (0 = never).
CONF_RELOCK_SECONDS: Final = "relock_seconds"
CONF_ADMIN_ONLY: Final = "open_admin_only"

# Live view
CONF_LIVE_SECONDS: Final = "live_seconds"
CONF_AFTER_OPEN_SECONDS: Final = "after_open_seconds"
CONF_SOUND: Final = "sound"
CONF_VOLUME: Final = "volume"
CONF_TALK: Final = "talk"
CONF_PRELOAD: Final = "camera_preload"
# Ring picture for push and Telegram: where it comes from, and how long after the ring.
CONF_SNAPSHOT_SOURCE: Final = "snapshot_source"
CONF_SNAPSHOT_DELAY: Final = "snapshot_delay"
SNAPSHOT_AUTO: Final = "auto"  # the camera's still address if it has one, else the live stream
SNAPSHOT_STREAM: Final = "stream"
SNAPSHOT_STILL: Final = "still"
SNAPSHOT_DELAY_MAX: Final = 5
# Open by NFC tag (Home Assistant app): tag entities, allowed phones (device ids),
# optional time window ("HH:MM"), message to the push receivers after opening.
CONF_NFC_TAGS: Final = "nfc_tags"
CONF_NFC_DEVICES: Final = "nfc_devices"
CONF_NFC_FROM: Final = "nfc_from"
CONF_NFC_UNTIL: Final = "nfc_until"
CONF_NFC_NOTIFY: Final = "nfc_notify"

# Notifications
CONF_NOTIFY: Final = "notify_services"
# Special entry in CONF_NOTIFY: every phone with the companion app, also ones added later.
# Old "all phones" choice: no longer offered in the editor, still honoured for doors that use it.
NOTIFY_ALL_MOBILE: Final = "all_mobile_apps"
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
SECTION_NFC: Final = "nfc"

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
    CONF_RELOCK_SECONDS: 10.0,
    CONF_ADMIN_ONLY: False,
    CONF_LIVE_SECONDS: 60,
    CONF_AFTER_OPEN_SECONDS: 20,
    CONF_SOUND: SOUND_DINGDONG,
    CONF_VOLUME: 70,
    CONF_TALK: False,
    CONF_PRELOAD: True,
    CONF_SNAPSHOT_SOURCE: SNAPSHOT_AUTO,
    CONF_SNAPSHOT_DELAY: 0,
    CONF_NFC_TAGS: [],
    CONF_NFC_DEVICES: [],
    CONF_NFC_FROM: None,
    CONF_NFC_UNTIL: None,
    CONF_NFC_NOTIFY: False,
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

HISTORY_SIZE: Final = 50
# Door openings are kept across restarts (who opened, and how).
OPEN_HISTORY_SIZE: Final = 50
PANEL_URL: Final = "portier"
STATIC_URL: Final = "/portier_static"
