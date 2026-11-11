"""A fake board for the integration's tests: its CoAP answers, in place of the network."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.ha_coap import coap

pytest_plugins = "pytest_homeassistant_custom_component"

HOST = "fd2a:83c:28ba:1::9"
DEVICE_ID = "1a2b3c4d"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Home Assistant loads custom_components/ha_coap in every test."""
    yield


class Board:
    """A board's resources: 'data', 'info', 'pumpdc', 'pump', 'ping'. Offline: no answer
    (TimeoutError). Records the requests it gets."""

    def __init__(self) -> None:
        self.online = True
        self.soil, self.battery, self.air, self.temperature = 42, 87, 55, -3
        self.info = f"b75c209,2.0,{DEVICE_ID}"
        self.pump_s = 3
        self.requests: list[tuple[str, str, int, bytes]] = []

    async def request(self, host, path, method=coap.GET, payload=b"", **kwargs):
        self.requests.append((host, path, method, payload))
        if not self.online:
            raise TimeoutError(f"no answer from {host} for '{path}'")
        if path == "data":
            return coap.CONTENT, bytes([self.soil, self.battery, self.air, self.temperature & 0xFF])
        if path == "info":
            return coap.CONTENT, self.info.encode() + b"\0"
        if path == "pumpdc":
            if method == coap.PUT:
                self.pump_s = int(payload.decode())
            return coap.CONTENT, bytes([self.pump_s])
        if path == "pump":
            return coap.CONTENT, b"\x01"
        if path == "ping":
            return coap.CHANGED, b""
        return coap.NOT_FOUND, b""

    def asked(self, path, method=None):
        return [r for r in self.requests if r[1] == path and (method is None or r[2] == method)]


@pytest.fixture
def board():
    fake = Board()
    with patch("custom_components.ha_coap.coap.request", side_effect=fake.request):
        yield fake
