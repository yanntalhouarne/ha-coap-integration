"""HA CoAP: plant boards (ha-coap nodes) on a Thread network, over CoAP.

Each board is a config entry (found by zeroconf, or added by its address). Its coordinator asks
it for its readings and gets its pushes; its entities show them, run its pump and beep it. One more
entry can connect the fleet manager (the ha-coap-server app): each board then has a firmware
update in Home Assistant's Updates, each plant its water tank and its battery's days left, and the
plants' care tasks are a to-do list.

The integration also serves a dashboard card, HA CoAP plant (custom:ha-coap-plant-card), loaded by
the frontend: no resource to add. Browsers check it at each page load, so an update that changes
only the card shows after a reload, without restarting Home Assistant.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from homeassistant.const import CONF_HOST, CONF_ID, CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import coap
from .const import DOMAIN, board_id, clean_host
from .coordinator import HaCoapConfigEntry, HaCoapCoordinator
from .fleet import FleetCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.EVENT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = "/ha_coap/ha-coap-plant-card.js"
CARD_FILE = Path(__file__).parent / "frontend" / "ha-coap-plant-card.js"

FLEET_PLATFORMS = [Platform.UPDATE, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.TODO]


class PlantCardView(HomeAssistantView):
    """The plant card's file, as it is on disk now. Browsers keep it but ask again at each page
    load ("no-cache"; unchanged, the answer is a short 304), so a new card installed by an update
    shows after a reload, without a restart."""

    url = CARD_URL
    name = "ha_coap:plant_card"
    requires_auth = False  # loaded by the frontend's page, like its own scripts

    async def get(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(CARD_FILE, headers={"Cache-Control": "no-cache"})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The plant card: served here, and loaded by the frontend."""
    if hass.http is None or "frontend" not in hass.config.components:
        return True  # no web server (tests), or no frontend
    from homeassistant.components.frontend import add_extra_js_url

    version = (await async_get_integration(hass, DOMAIN)).version
    hass.http.register_view(PlantCardView())
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    return True


def is_fleet_entry(entry) -> bool:
    """The fleet manager's entry (its address and key), not a board's."""
    return CONF_URL in entry.data


def _shared(hass: HomeAssistant) -> dict:
    """What the boards share: one observer socket, and the board at each address (for its pushes)."""
    return hass.data.setdefault(DOMAIN, {"observer": None, "boards": {}})


def _observer(hass: HomeAssistant) -> coap.Observer:
    shared = _shared(hass)
    if shared["observer"] is None:
        def on_notification(host: str, payload: bytes) -> None:
            board = shared["boards"].get(host)
            if board is not None:
                board.on_push(payload)
        shared["observer"] = coap.Observer(on_notification)
    return shared["observer"]


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry) -> bool:
    """A board: its entities at once, then its first reading in the background (a board that
    doesn't answer doesn't hold up Home Assistant: its entities show unavailable). The fleet
    manager: its firmware updates."""
    if is_fleet_entry(entry):
        fleet = FleetCoordinator(hass, entry)
        entry.runtime_data = fleet
        await hass.config_entries.async_forward_entry_setups(entry, FLEET_PLATFORMS)
        entry.async_create_background_task(hass, fleet.async_refresh(), f"{DOMAIN} fleet manager's firmware and plants")
        return True
    coordinator = HaCoapCoordinator(hass, entry, _observer(hass))
    entry.runtime_data = coordinator
    _shared(hass)["boards"][coordinator.host] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, coordinator.async_refresh(), f"{DOMAIN} first reading of {coordinator.name}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HaCoapConfigEntry) -> bool:
    """Unloading (and so reloading, when it's found at a new address) works."""
    if is_fleet_entry(entry):
        return await hass.config_entries.async_unload_platforms(entry, FLEET_PLATFORMS)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        shared = _shared(hass)
        coordinator = entry.runtime_data
        if shared["boards"].get(coordinator.host) is coordinator:
            del shared["boards"][coordinator.host]
        if shared["observer"] is not None:
            shared["observer"].forget(coordinator.host)
            if not shared["boards"]:
                shared["observer"].close()
                shared["observer"] = None
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HaCoapConfigEntry) -> None:
    """A removed board leaves no issue behind."""
    ir.async_delete_issue(hass, DOMAIN, f"unreachable_{entry.entry_id}")


async def async_migrate_entry(hass: HomeAssistant, entry: HaCoapConfigEntry) -> bool:
    """Version 1 → 2: the board's real ID ("1a2b3c4d", not "coap-1a2b3c4d": version 1 took what
    follows the first dash of "ha-coap-1a2b3c4d") in the entry, its device and its entities'
    unique IDs; its address without brackets. The entity IDs, and so their history, don't change."""
    if entry.version > 2:
        return False  # from a newer version of the integration
    if entry.version == 2 or is_fleet_entry(entry):
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
