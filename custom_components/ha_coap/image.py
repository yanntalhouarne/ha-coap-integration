"""Each plant's photo, from the fleet manager (for its card)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .plants import add_plant_entities, photo_images


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    add_plant_entities(hass, entry, async_add_entities, photo_images)  # only the fleet manager's entry has it
