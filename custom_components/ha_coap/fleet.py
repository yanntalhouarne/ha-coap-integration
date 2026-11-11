"""The fleet manager (the ha-coap-server app): what firmware each board runs, the release image,
and updating a board to it. Through a link made for Home Assistant in the app (Settings, Other
devices, "For the HA CoAP integration"), which allows only that."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

INTERVAL = timedelta(minutes=30)
INTERVAL_UPDATING = timedelta(seconds=10)  # while an update runs: its progress
KEY_HEADER = "X-HACoap-Key"                # the app's link key
CHANGE_HEADER = {"X-HACoap": "1"}          # the app asks it of every change
TIMEOUT = aiohttp.ClientTimeout(total=20)


class FleetError(Exception):
    """The fleet manager didn't answer, or refused (auth: its key was revoked)."""

    def __init__(self, message: str, auth: bool = False) -> None:
        super().__init__(message)
        self.auth = auth


async def fetch(session: aiohttp.ClientSession, url: str, key: str, method: str = "GET",
                body: dict | None = None) -> Any:
    """A request to the fleet manager's API; its JSON answer. Raises FleetError."""
    headers = {KEY_HEADER: key, **(CHANGE_HEADER if method != "GET" else {})}
    try:
        async with session.request(method, url, json=body, headers=headers, timeout=TIMEOUT) as response:
            if response.status in (401, 403):
                raise FleetError("The fleet manager refused the key", auth=True)
            data = await response.json(content_type=None)
            if response.status != 200:
                raise FleetError((data or {}).get("detail") or f"HTTP {response.status}")
            return data
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise FleetError(f"No answer from the fleet manager: {err}") from err


class FleetCoordinator(DataUpdateCoordinator[dict]):
    """The fleet manager's /api/ha/firmware: {release, running, devices: [{device_id, image_hash,
    git_commit, update}]}, every 30 minutes (every 10 s while an update runs)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name="fleet manager", update_interval=INTERVAL)
        self.base = entry.data[CONF_URL].rstrip("/")
        self.key = entry.data[CONF_API_KEY]

    async def _async_update_data(self) -> dict:
        try:
            data = await fetch(async_get_clientsession(self.hass), f"{self.base}/api/ha/firmware", self.key)
        except FleetError as err:
            raise UpdateFailed(str(err)) from err
        updating = data.get("running") or any(
            (d.get("update") or {}).get("status") in ("queued", "running") for d in data.get("devices", []))
        self.update_interval = INTERVAL_UPDATING if updating else INTERVAL
        return data

    def device(self, device_id: str) -> dict | None:
        return next((d for d in (self.data or {}).get("devices", []) if d["device_id"] == device_id), None)

    async def update(self, device_id: str) -> None:
        """Asks the fleet manager to update this board to the release image."""
        try:
            await fetch(async_get_clientsession(self.hass), f"{self.base}/api/ha/update", self.key, "POST",
                        {"device_id": device_id})
        except FleetError as err:
            raise HomeAssistantError(f"The fleet manager didn't start the update: {err}") from err
        self.update_interval = INTERVAL_UPDATING
        await self.async_request_refresh()
