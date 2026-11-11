"""HA CoAP: plant boards (ha-coap nodes) on a Thread network, over CoAP.

Each board is a config entry (found by zeroconf, or added by its address). Its coordinator asks
it for its readings; its entities show them, run its pump and beep it.
"""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_ID, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, board_id, clean_host
from .coordinator import HaCoapConfigEntry, HaCoapCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry) -> bool:
    """Its entities at once, then its first reading in the background: a board that doesn't
    answer doesn't hold up Home Assistant (its entities show unavailable)."""
    coordinator = HaCoapCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, coordinator.async_refresh(), f"{DOMAIN} first reading of {coordinator.name}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HaCoapConfigEntry) -> bool:
    """Unloading (and so reloading, when it's found at a new address) works."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: HaCoapConfigEntry) -> bool:
    """Version 1 → 2: the board's real ID ("1a2b3c4d", not "coap-1a2b3c4d": version 1 took what
    follows the first dash of "ha-coap-1a2b3c4d") in the entry, its device and its entities'
    unique IDs; its address without brackets. The entity IDs, and so their history, don't change."""
    if entry.version > 2:
        return False  # from a newer version of the integration
    if entry.version == 2:
        return True
    old = entry.unique_id or entry.data.get(CONF_ID) or ""
    if not old:  # no ID at all: nothing to rename
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: clean_host(entry.data.get(CONF_HOST))},
                                               version=2)
        return True
    new = board_id(old)
    taken = any(other.unique_id == new for other in hass.config_entries.async_entries(DOMAIN) if other is not entry)
    real = bool(new) and not taken  # the board's own ID, and free
    if not real:
        # Its ID stays (its entities still get the "<ID>_<key>" form the entities use now)
        _LOGGER.warning("%s: its ID %r is kept (%s)", entry.title, old,
                        "another entry has the board's ID" if taken else "no board ID in it")
        new = old
    data = {**entry.data, CONF_HOST: clean_host(entry.data.get(CONF_HOST)), CONF_ID: new}
    entities = er.async_get(hass)

    @callback
    def rename(entity: er.RegistryEntry) -> dict | None:
        """"<old><key>" or "<old>_<key>" → "<new>_<key>" (also when the ID itself was right: a
        board added by hand with its ID, whose entities lack the underscore)."""
        if not entity.unique_id.startswith(old):
            return None
        unique_id = f"{new}_{entity.unique_id[len(old):].lstrip('_')}"
        if unique_id == entity.unique_id:
            return None
        if entities.async_get_entity_id(entity.domain, DOMAIN, unique_id):
            _LOGGER.warning("%s: %s keeps its unique ID (%s is taken)", entry.title, entity.entity_id, unique_id)
            return None
        return {"new_unique_id": unique_id}

    await er.async_migrate_entries(hass, entry.entry_id, rename)
    if new != old:
        devices = dr.async_get(hass)
        device = devices.async_get_device(identifiers={(DOMAIN, old)})
        if device is not None:
            devices.async_update_device(device.id, new_identifiers={(DOMAIN, new)})
    hass.config_entries.async_update_entry(entry, unique_id=new if real else entry.unique_id, data=data, version=2)
    _LOGGER.info("%s: migrated to version 2 (board ID %s, was %s)", entry.title, new, old)
    return True
