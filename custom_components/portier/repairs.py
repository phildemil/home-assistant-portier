"""Repair: add the loader to configuration.yaml with one click."""

from __future__ import annotations

from pathlib import Path

import probatio as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .yaml_setup import LOADER_URL, CannotEdit, install_loader


class LoaderRepairFlow(RepairsFlow):
    """Ask, then edit configuration.yaml, then ask for a restart."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
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
            self.hass.data.setdefault(f"{DOMAIN}_frontend", {})["yaml_written"] = True
            return await self.async_step_restart()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"url": LOADER_URL},
        )

    async def async_step_restart(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            ir.async_delete_issue(self.hass, DOMAIN, "loader_missing")
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="restart", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    return LoaderRepairFlow()
