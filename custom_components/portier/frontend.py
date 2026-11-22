"""Serve the frontend module and register the side panel."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from aiohttp import web

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, PANEL_URL, STATIC_URL
from .yaml_setup import BACKUP_SUFFIX, LOADER_URL, install_loader, uninstall_loader

_DIR = Path(__file__).parent / "frontend"
_FILE = "portier.js"
_KEY = f"{DOMAIN}_frontend"
_LOGGER = logging.getLogger(__name__)
CURRENT_URL = f"{STATIC_URL}/current"

# Served from config/www, which Home Assistant makes available from the very start,
# long before integrations are loaded. It waits until Portier is up, then loads it.
LOADER_JS = """// Portier loader – created automatically, do not edit.
(async () => {
  // Waits for Portier while Home Assistant starts; gives up after ~10 minutes
  // (e.g. Portier was removed but this line is still in configuration.yaml).
  for (let i = 0; i < 200; i++) {
    if (window.__portier) return;
    try {
      const r = await fetch("%s", { cache: "no-store", credentials: "same-origin" });
      if (r.ok) {
        const { url } = await r.json();
        if (!window.__portier) await import(url);
        return;
      }
    } catch (e) {
      /* Home Assistant is still starting */
    }
    await new Promise((res) => setTimeout(res, 3000));
  }
})();
""" % CURRENT_URL


class CurrentView(HomeAssistantView):
    """Tells the loader which Portier file is current."""

    url = CURRENT_URL
    name = "portier:current"
    requires_auth = False

    def __init__(self, data: dict) -> None:
        self._data = data

    async def get(self, request: web.Request) -> web.Response:
        if not self._data.get("active"):
            return web.Response(status=503)
        return self.json({"url": self._data["url"]}, headers={"Cache-Control": "no-store"})


def _version() -> str:
    return hashlib.sha256((_DIR / _FILE).read_bytes()).hexdigest()[:10]


def _write_loader(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != LOADER_JS:
        path.write_text(LOADER_JS, encoding="utf-8")


ISSUE_MISSING = "loader_missing"


async def async_ensure_loader(hass: HomeAssistant) -> None:
    """Write the loader file and the configuration.yaml line, once, right away.

    Every Home Assistant start reads configuration.yaml first, so the line only
    has to be there before the next start – no extra restart is needed. Only if
    the file cannot be changed safely, a notice explains the manual step.
    """
    def _install() -> bool:
        _write_loader(Path(hass.config.path("www", "portier", "loader.js")))
        return install_loader(Path(hass.config.path("configuration.yaml")))

    try:
        changed = await hass.async_add_executor_job(_install)
    except Exception as err:  # noqa: BLE001 – never stop a door from starting over this
        _LOGGER.warning(
            "Portier: Zeile %s konnte nicht in configuration.yaml eingetragen werden (%s)",
            LOADER_URL,
            err,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_MISSING,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MISSING,
            translation_placeholders={"url": LOADER_URL},
        )
        return
    ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING)
    if changed:
        _LOGGER.info("Portier: Lader in configuration.yaml eingetragen (Sicherung: %s)", BACKUP_SUFFIX)


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Make the overlay available on every HA page and add the side panel."""
    data = hass.data.setdefault(_KEY, {})
    if data.get("active"):
        return
    if not data.get("static"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(_DIR), cache_headers=False)]
        )
        data["static"] = True
    version = await hass.async_add_executor_job(_version)
    url = f"{STATIC_URL}/{_FILE}?v={version}"
    data["url"] = url
    frontend.add_extra_js_url(hass, url)
    if not data.get("view"):
        hass.http.register_view(CurrentView(data))
        data["view"] = True
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="portier-panel",
        sidebar_title="Türklingel",
        sidebar_icon="mdi:doorbell-video",
        module_url=url,
        require_admin=False,
    )
    data.update(active=True, url=url)


async def async_door_added(hass: HomeAssistant) -> None:
    """A door was set up: make sure the loader is in place (first door, or re-added)."""
    await async_ensure_loader(hass)


async def async_remove_loader(hass: HomeAssistant) -> None:
    """Undo the one-time setup: configuration.yaml line, loader file, notices."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING)

    def _remove() -> bool:
        changed = False
        try:
            changed = uninstall_loader(Path(hass.config.path("configuration.yaml")))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Portier: Zeile %s bitte selbst aus configuration.yaml entfernen (%s)",
                LOADER_URL,
                err,
            )
        folder = Path(hass.config.path("www", "portier"))
        (folder / "loader.js").unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass  # not empty or already gone
        return changed

    if await hass.async_add_executor_job(_remove):
        _LOGGER.info("Portier: Lader aus configuration.yaml entfernt (gilt nach Neustart)")


@callback
def async_unregister_frontend(hass: HomeAssistant) -> None:
    data = hass.data.get(_KEY, {})
    if not data.get("active"):
        return
    frontend.remove_extra_js_url(hass, data["url"])
    frontend.async_remove_panel(hass, PANEL_URL)
    data["active"] = False
