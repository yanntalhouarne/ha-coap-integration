"""The fleet manager (the ha-coap-server app): what firmware each board runs, the release image,
and updating a board to it; each plant's water tank, battery forecast and care tasks, a tank
refilled and a care task done. Through a link made for Home Assistant in the app (Settings, Other
devices, "For the HA CoAP integration"), which allows only that."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

INTERVAL = timedelta(minutes=5)
INTERVAL_UPDATING = timedelta(seconds=10)  # while an update runs: its progress
KEY_HEADER = "X-HACoap-Key"                # the app's link key
CHANGE_HEADER = {"X-HACoap": "1"}          # the app asks it of every change
TIMEOUT = aiohttp.ClientTimeout(total=20)


def _slug(key: str) -> str:
    """An alert's key as part of an issue's ID: 'router-gone:0x5c00' → 'router_gone_0x5c00'."""
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


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
    git_commit, update}]}, with "plants" from its /api/ha/plants: {device_id: {name, on_usb,
    battery_days_left, tank, care}}, every 5 minutes (every 10 s while an update runs)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name="fleet manager", update_interval=INTERVAL)
        self.base = entry.data[CONF_URL].rstrip("/")
        self.key = entry.data[CONF_API_KEY]

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            data = await fetch(session, f"{self.base}/api/ha/firmware", self.key)
        except FleetError as err:
            raise UpdateFailed(str(err)) from err
        plants = None
        try:
            plants = await fetch(session, f"{self.base}/api/ha/plants", self.key)
            data["plants"] = {plant["device_id"]: plant for plant in plants.get("devices", [])}
        except FleetError as err:
            # Refused: a fleet manager older than its plants (none). Else: as they were
            _LOGGER.debug("No plants from the fleet manager: %s", err)
            data["plants"] = {} if err.auth else self.plants()
        self._fleet_issues(plants.get("alerts") if isinstance(plants, dict) else None)
        updating = data.get("running") or any(
            (d.get("update") or {}).get("status") in ("queued", "running") for d in data.get("devices", []))
        self.update_interval = INTERVAL_UPDATING if updating else INTERVAL
        return data

    def _fleet_issues(self, alerts: list[dict] | None) -> None:
        """The fleet manager's open alerts about no board (the border router down, a router gone, no
        board answering...) as repairs, gone once it clears them. None: it didn't say (older, or
        unreachable): the issues stay as they were."""
        if alerts is None:
            return
        wanted = {f"fleet_alert_{_slug(alert['key'])}": alert for alert in alerts}
        for issue_id, alert in wanted.items():
            ir.async_create_issue(self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                  translation_key="fleet_alert", translation_placeholders={"message": alert["message"]})
        # The ones raised before (since a restart too: the repairs are kept) that cleared
        raised = {issue_id for domain, issue_id in ir.async_get(self.hass).issues if domain == DOMAIN
                  and issue_id.startswith("fleet_alert_")}
        for issue_id in raised - wanted.keys():
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def device(self, device_id: str) -> dict | None:
        return next((d for d in (self.data or {}).get("devices", []) if d["device_id"] == device_id), None)

    def plants(self) -> dict[str, dict]:
        return (self.data or {}).get("plants") or {}

    async def _change(self, path: str, what: str) -> None:
        try:
            await fetch(async_get_clientsession(self.hass), f"{self.base}{path}", self.key, "POST", {})
        except FleetError as err:
            raise HomeAssistantError(f"The fleet manager didn't take {what}: {err}") from err
        await self.async_request_refresh()

    async def photo(self, device_id: str) -> tuple[bytes, str] | None:
        """Its plant's newest photo and its type, or None (none, or no answer)."""
        try:
            async with async_get_clientsession(self.hass).get(
                    f"{self.base}/api/devices/{device_id}/photo", headers={KEY_HEADER: self.key}, timeout=TIMEOUT) as response:
                if response.status != 200:
                    return None
                return await response.read(), response.content_type or "image/jpeg"
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("No photo of %s from the fleet manager: %s", device_id, err)
            return None

    async def refilled(self, device_id: str) -> None:
        """Its plant's water tank is full again."""
        await self._change(f"/api/devices/{device_id}/tank/refilled", "the tank refilled")

    async def care_done(self, task_id: int) -> None:
        """A care task done: due again its number of days from now."""
        await self._change(f"/api/care/{task_id}/done", "the care task done")

    async def update(self, device_id: str) -> None:
        """Asks the fleet manager to update this board to the release image."""
        try:
            await fetch(async_get_clientsession(self.hass), f"{self.base}/api/ha/update", self.key, "POST",
                        {"device_id": device_id})
        except FleetError as err:
            raise HomeAssistantError(f"The fleet manager didn't start the update: {err}") from err
        self.update_interval = INTERVAL_UPDATING
        await self.async_request_refresh()
