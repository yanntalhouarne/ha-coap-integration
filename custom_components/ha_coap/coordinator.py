"""One board: its address, its latest readings, and the requests to it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import coap
from .const import DOMAIN, INFO_EVERY, UPDATE_INTERVAL, board_id, clean_host

_LOGGER = logging.getLogger(__name__)

ERRORS = (TimeoutError, OSError, coap.CoapError)

type HaCoapConfigEntry = ConfigEntry[HaCoapCoordinator]


@dataclass
class BoardState:
    """What the board last said. 'info' and 'pump_s' are None until it answered them."""

    data: coap.Data
    info: coap.Info | None = None
    pump_s: int | None = None  # its pump's run time (s), 1 to 9


class HaCoapCoordinator(DataUpdateCoordinator[BoardState]):
    """Asks the board for its readings every UPDATE_INTERVAL, and for its info and pump run time
    every INFO_EVERY times. Its entities show unavailable while it doesn't answer."""

    config_entry: HaCoapConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HaCoapConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=entry.title or entry.data[CONF_NAME],
                         update_interval=UPDATE_INTERVAL)
        self.host = clean_host(entry.data[CONF_HOST])
        self.device_id: str = entry.data[CONF_ID]
        self._updates = 0

    async def _async_update_data(self) -> BoardState:
        try:
            data = coap.parse_data(await coap.get(self.host, "data"))
        except ERRORS as err:
            raise UpdateFailed(f"No answer from {self.name} ({self.host}): {err}") from err
        previous = self.data
        info = previous.info if previous else None
        pump_s = previous.pump_s if previous else None
        if info is None or pump_s is None or self._updates % INFO_EVERY == 0:
            info = await self._read_info() or info
            pump_s = await self.read_pump_s() or pump_s
        self._updates += 1
        return BoardState(data, info, pump_s)

    async def _read_info(self) -> coap.Info | None:
        """Its info, noted on its device (firmware, hardware). None when it didn't answer."""
        try:
            info = coap.parse_info(await coap.get(self.host, "info"))
        except ERRORS as err:
            _LOGGER.debug("%s: no 'info' (%s)", self.name, err)
            return None
        expected = board_id(self.device_id)  # also in an ID kept from version 1, such as "coap-1a2b3c4d"
        if expected and info.device_id and info.device_id != expected:
            # Another board answers at this address: its readings aren't this plant's
            raise UpdateFailed(f"{self.host} is board {info.device_id} now, not {self.device_id}: "
                               f"it will be found again at its new address")
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.device_id)})
        if device and (device.sw_version, device.hw_version) != (info.fw_version, info.hw_version or None):
            registry.async_update_device(device.id, sw_version=info.fw_version, hw_version=info.hw_version or None)
        return info

    async def read_pump_s(self) -> int | None:
        try:
            payload = await coap.get(self.host, "pumpdc")
        except ERRORS as err:
            _LOGGER.debug("%s: no 'pumpdc' (%s)", self.name, err)
            return None
        return payload[0] if payload and 1 <= payload[0] <= 9 else None

    async def put(self, path: str, payload: bytes) -> bytes:
        """A PUT to the board, for a button or a setting. Raises HomeAssistantError when it
        doesn't answer (the page shows it)."""
        try:
            return await coap.put(self.host, path, payload)
        except ERRORS as err:
            raise HomeAssistantError(f"{self.name} didn't answer: {err}") from err

    async def set_pump_s(self, seconds: int) -> int:
        """Sets its pump's run time (1 to 9 s); returns the one it uses now."""
        answer = await self.put("pumpdc", str(seconds).encode())
        value = answer[0] if answer and 1 <= answer[0] <= 9 else seconds
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, pump_s=value))
        return value
