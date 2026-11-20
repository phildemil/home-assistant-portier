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
from .yaml_setup import LOADER_URL

_DIR = Path(__file__).parent / "frontend"
_FILE = "portier.js"
_KEY = f"{DOMAIN}_frontend"
_LOGGER = logging.getLogger(__name__)
CURRENT_URL = f"{STATIC_URL}/current"

# Served from config/www, which Home Assistant makes available from the very start,
# long before integrations are loaded. It waits until Portier is up, then loads it.
LOADER_JS = """// Portier loader – created automatically, do not edit.
(async () => {
  for (;;) {
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
ISSUE_RESTART = "loader_restart"


def loader_configured(hass: HomeAssistant) -> bool:
    """True if configuration.yaml loads the Portier loader on every page."""
    manager = hass.data.get(frontend.DATA_EXTRA_MODULE_URL)
    return bool(manager) and LOADER_URL in getattr(manager, "urls", ())


@callback
def _async_check_loader(hass: HomeAssistant) -> None:
    """Keep a repair notice until the loader really runs (line written AND restarted)."""
    if loader_configured(hass):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING)
        ir.async_delete_issue(hass, DOMAIN, ISSUE_RESTART)
        return
    if hass.data.get(_KEY, {}).get("yaml_written"):
        # The line is in configuration.yaml but only counts after a restart.
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING)
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_RESTART,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_RESTART,
        )
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_MISSING,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MISSING,
        translation_placeholders={"url": LOADER_URL},
    )


@callback
def async_loader_written(hass: HomeAssistant) -> None:
    """The repair wrote the line: from now on only a restart is missing."""
    hass.data.setdefault(_KEY, {})["yaml_written"] = True
    _async_check_loader(hass)


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
    try:
        await hass.async_add_executor_job(
            _write_loader, Path(hass.config.path("www", "portier", "loader.js"))
        )
    except OSError:
        _LOGGER.warning("Portier: Lader konnte nicht nach config/www geschrieben werden", exc_info=True)
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
    _async_check_loader(hass)


@callback
def async_unregister_frontend(hass: HomeAssistant) -> None:
    data = hass.data.get(_KEY, {})
    if not data.get("active"):
        return
    frontend.remove_extra_js_url(hass, data["url"])
    frontend.async_remove_panel(hass, PANEL_URL)
    data["active"] = False
