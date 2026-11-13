"""The plants' care tasks (from the fleet manager), as one to-do list: Plant care."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .plants import PlantCare


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PlantCare(entry.runtime_data, entry)])  # only the fleet manager's entry has it
