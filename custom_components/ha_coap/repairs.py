"""Repairs: a board that hasn't answered for a day (its address may be dead: a new border router
gives every board a new one, and discovery may not have seen it yet). The fix: its new address
(it must be the same board), or removing it."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.const import CONF_HOST, CONF_ID
from homeassistant.core import HomeAssistant

from .config_flow import board_at
from .const import board_id, clean_host


class UnreachableFlow(RepairsFlow):
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return self.async_create_entry(data={})  # removed meanwhile: nothing to fix
        errors: dict[str, str] = {}
        if user_input is not None:
            host = clean_host(user_input[CONF_HOST])
            device_id, error = await board_at(host)
            if error:
                errors["base"] = error
            elif device_id != board_id(entry.data[CONF_ID]):
                errors["base"] = "another_board"
            else:
                self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: host})
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            description_placeholders={"name": entry.title},
            errors=errors,
        )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    return UnreachableFlow(str((data or {}).get("entry_id", "")))
