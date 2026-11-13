"""A fake board for the integration's tests: its CoAP answers and its pushes, in place of the
network."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.ha_coap import coap

pytest_plugins = "pytest_homeassistant_custom_component"

HOST = "fd2a:83c:28ba:1::9"
DEVICE_ID = "1a2b3c4d"
POWER_CODES = {name: i for i, name in enumerate(coap.POWER_NAMES)}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Home Assistant loads custom_components/ha_coap in every test."""
    yield


class Board:
    """A board's resources: 'data', 'info', 'pumpdc', 'pump', 'ping', and with newer firmware
    'diag' and 'hist'. Offline: no answer (TimeoutError). Records the requests it gets."""

    def __init__(self) -> None:
        self.online = True
        self.soil, self.battery, self.air, self.temperature = 42, 87, 55, -3
        self.info = f"b75c209,2.0,{DEVICE_ID}"
        self.pump_s = 3
        self.requests: list[tuple[str, str, int, bytes]] = []
        # Newer firmware ('diag', 'hist'); None: older firmware without them
        self.diag: dict | None = {"boots": 7, "up": 3600, "reset": "power", "role": "child", "rloc": "f802",
                                  "parent": "f800", "rssi": -71, "lq": 3, "soil_mv": 900, "power": "battery",
                                  "button_s": 0, "pump_ms": 0, "observers": 3}
        self.diag_pump_s = True  # its 'diag' tells its pump's run time (newer firmware)
        self.samples: list[tuple[int, int, int, int, int, str]] | None = []  # (uptime, soil, battery, air, temp, power)
        self.boot = 7

    def add_sample(self, uptime_s: int, soil: int, power: str = "battery") -> None:
        self.samples.append((uptime_s, soil, self.battery, self.air, self.temperature, power))

    def history_payload(self, first: int) -> bytes:
        kept = self.samples[first:first + 54]
        data = coap.HISTORY_HEADER.pack(1, len(kept), coap.HISTORY_ENTRY.size, self.boot, self.diag["up"] if self.diag else 0,
                                        first, len(self.samples))
        for uptime, soil, battery, air, temperature, power in kept:
            data += coap.HISTORY_ENTRY.pack(uptime, soil, battery, air, temperature, POWER_CODES[power])
        return data

    async def request(self, host, path, method=coap.GET, payload=b"", query=(), **kwargs):
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
            if method == coap.PUT and payload == b"1" and self.diag is not None:
                self.diag["pump_ms"] += self.pump_s * 1000
            return coap.CONTENT, b"\x01"
        if path == "ping":
            return coap.CHANGED, b""
        if path == "diag" and self.diag is not None:
            fields = {**self.diag, **({"pump_s": self.pump_s} if self.diag_pump_s else {})}
            return coap.CONTENT, ",".join(f"{k}={v}" for k, v in fields.items()).encode()
        if path == "hist" and self.samples is not None:
            first = next((int(q[5:]) for q in query if q.startswith("from=")), 0)
            return coap.CONTENT, self.history_payload(first)
        return coap.NOT_FOUND, b""

    def asked(self, path, method=None):
        return [r for r in self.requests if r[1] == path and (method is None or r[2] == method)]


class FakeObserver:
    """coap.Observer without a socket: registrations go to the fake board, pushes come from push()."""

    instances: list[FakeObserver] = []

    def __init__(self, on_notification) -> None:
        self.on_notification = on_notification
        self.registered: list[tuple[str, tuple]] = []
        self.closed = False
        self.board: Board | None = None
        FakeObserver.instances.append(self)

    async def register(self, host, path="hist", query=(), **kwargs):
        self.registered.append((host, query))
        code, payload = await self.board.request(host, path, coap.GET, query=query)
        return code, payload, code == coap.CONTENT

    def forget(self, host) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def push(self, host, payload) -> None:
        self.on_notification(host, payload)


@pytest.fixture
def board():
    fake = Board()

    def observer(on_notification):
        made = FakeObserver(on_notification)
        made.board = fake
        return made

    FakeObserver.instances = []
    with patch("custom_components.ha_coap.coap.request", side_effect=fake.request), \
            patch("custom_components.ha_coap.coap.Observer", side_effect=observer):
        yield fake
