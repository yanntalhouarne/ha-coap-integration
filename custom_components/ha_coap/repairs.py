"""Repairs: a board that hasn't answered for a day (its address may be dead: a new border router
gives every board a new one, and discovery may not have seen it yet). The fix, from a menu: its
new address (it must be the same board), "it's off for now" (no issue until it has answered
again), or removing it."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_ID
from homeassistant.core import HomeAssistant

from .config_flow import board_at
from .const import QUIET_WHILE_OFF, board_id, clean_host


class UnreachableFlow(RepairsFlow):
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id

    def _entry(self) -> ConfigEntry | None:
        return self.hass.config_entries.async_get_entry(self.entry_id)

    @staticmethod
    def _placeholders(entry: ConfigEntry) -> dict[str, str]:
        return {"name": entry.title, "host": entry.data[CONF_HOST]}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        entry = self._entry()
        if entry is None:
            return self.async_create_entry(data={})  # removed meanwhile: nothing to fix
        return self.async_show_menu(step_id="init", menu_options=["address", "off", "remove"],
                                    description_placeholders=self._placeholders(entry))

    async def async_step_address(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Its new address: the same board must answer there."""
        entry = self._entry()
        if entry is None:
            return self.async_create_entry(data={})
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
            step_id="address",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            description_placeholders=self._placeholders(entry),
            errors=errors,
        )

    async def async_step_off(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Turned off for now: no issue until it has answered again (its entities stay unavailable)."""
        entry = self._entry()
        if entry is not None:
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, QUIET_WHILE_OFF: True})
        return self.async_create_entry(data={})

    async def async_step_remove(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Gone for good: its entry, its device and its entities are removed."""
        entry = self._entry()
        if entry is None:
            return self.async_create_entry(data={})
        if user_input is None:
            return self.async_show_form(step_id="remove", data_schema=vol.Schema({}),
                                        description_placeholders=self._placeholders(entry))
        await self.hass.config_entries.async_remove(entry.entry_id)
        return self.async_create_entry(data={})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    return UnreachableFlow(str((data or {}).get("entry_id", "")))
