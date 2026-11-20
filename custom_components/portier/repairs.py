"""Repair: add the loader to configuration.yaml and restart, with one click."""

from __future__ import annotations

from pathlib import Path

import probatio as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .frontend import ISSUE_RESTART, async_loader_written
from .yaml_setup import LOADER_URL, CannotEdit, install_loader


class LoaderRepairFlow(RepairsFlow):
    """Ask, edit configuration.yaml, then restart Home Assistant.

    The notice only goes away once HA really runs with the loader (after the
    restart), so nobody ends up with a half-finished setup by accident.
    """

    def __init__(self, restart_only: bool = False) -> None:
        self._restart_only = restart_only

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if self._restart_only:
            return await self.async_step_restart()
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            path = Path(self.hass.config.path("configuration.yaml"))
            try:
                await self.hass.async_add_executor_job(install_loader, path)
            except (CannotEdit, OSError):
                return self.async_abort(
                    reason="cannot_edit", description_placeholders={"url": LOADER_URL}
                )
            async_loader_written(self.hass)
            return await self.async_step_restart()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"url": LOADER_URL},
        )

    async def async_step_restart(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            if self.hass.services.has_service("homeassistant", "restart"):
                self.hass.async_create_task(
                    self.hass.services.async_call("homeassistant", "restart")
                )
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="restart", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    return LoaderRepairFlow(restart_only=issue_id == ISSUE_RESTART)
