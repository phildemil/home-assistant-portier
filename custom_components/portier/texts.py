"""Texts Portier sends out (push, Telegram, notices), in the language of Home Assistant.

The German text is the key; any other language gets English. Messages go to several
people at once, so they follow the system language (Settings → System → General).
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

EN: dict[str, str] = {
    "Es klingelt · {time}": "Someone is ringing · {time}",
    "Es klingelte · {time}": "Someone rang · {time}",
    "Es klingelte · {time} · Öffnen nicht mehr möglich": "Someone rang · {time} · can no longer be opened",
    "⏱ Öffnen nicht mehr möglich": "⏱ Can no longer be opened",
    "✅ Tür geöffnet · {time} · {how}": "✅ Door opened · {time} · {how}",
    "🔓 Tür öffnen": "🔓 Open door",
    "⏱ Zu spät – Knopf abgelaufen": "⏱ Too late – button expired",
    "⚠️ Öffnen fehlgeschlagen": "⚠️ Opening failed",
    "✅ Tür geöffnet": "✅ Door opened",
    "von {who} {how}": "by {who} {how}",
    "über Home Assistant": "via Home Assistant",
    "über die App": "via the app",
    "über den Browser": "via the browser",
    "über Telegram": "via Telegram",
    "per NFC an der Tür": "by NFC at the door",
    "über {via}": "via {via}",
    "{who} hat per NFC geöffnet · {time}": "{who} opened by NFC · {time}",
    "Jemand": "Someone",
    "{name}: kein Türöffner eingerichtet": "{name}: no door opener set up",
    "Portier: Relais prüfen": "Portier: check the relay",
    "**{entity}** konnte nicht verriegelt werden. Portier versucht es weiter, bitte das Gerät prüfen.":
        "**{entity}** could not be locked. Portier keeps trying, please check the device.",
    "**{entity}** konnte nicht ausgeschaltet werden. Portier versucht es weiter, bitte das Gerät prüfen.":
        "**{entity}** could not be switched off. Portier keeps trying, please check the device.",
    "Türklingel": "Doorbell",
}


def german(hass: HomeAssistant) -> bool:
    return str(hass.config.language or "en").lower().startswith("de")


def tr(hass: HomeAssistant, text: str, **values: object) -> str:
    """The text in Home Assistant's language, with {placeholders} filled in."""
    if not german(hass):
        text = EN.get(text, text)
    return text.format(**values) if values else text
