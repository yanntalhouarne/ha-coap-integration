"""The myCoap integration."""

import logging

from homeassistant import config_entries, core

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "binary_sensor"]

async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up platform from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data
    # Forward the setup to the sensor platform.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Forward the switch to the sensor platform.
    # hass.async_create_task(
    #     hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # )
    return True


async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Set up the Custom component from yaml configuration."""
    hass.data.setdefault(DOMAIN, {})
    return True
