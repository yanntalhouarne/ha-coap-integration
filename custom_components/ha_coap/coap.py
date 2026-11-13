"""A small CoAP client (RFC 7252) for the boards' resources.

Only what the boards need: GET and PUT, confirmable requests with retransmission, piggybacked
or separate responses, and observing 'hist' (RFC 7641: Observer). The same client as the ha-coap
fleet manager's (manager/hacoap_manager/coap.py in the ha-coap-server repository). Timeouts are long because a sleepy board only gets a
message when it polls its parent (every few seconds).
"""

from __future__ import annotations

import asyncio
import os
import random
import socket
import struct
from dataclasses import dataclass

COAP_PORT = 5683

TYPE_CON, TYPE_NON, TYPE_ACK, TYPE_RST = 0, 1, 2, 3
GET, POST, PUT, DELETE = 1, 2, 3, 4
CHANGED, CONTENT, NOT_FOUND = 0x44, 0x45, 0x84
OPTION_OBSERVE, OPTION_URI_PATH, OPTION_URI_QUERY = 6, 11, 15

ACK_TIMEOUT_S = 4.0  # longer than a sleepy board's poll period
MAX_RETRANSMIT = 2   # then it gives up: 4 + 8 + 16 = 28 s at most


class CoapError(Exception):
    """A reset, an error answer, or an answer that doesn't make sense."""


@dataclass
class Info:
    """The board's 'info': the firmware's git commit, the hardware version and its device ID."""

    fw_version: str
    hw_version: str
    device_id: str


@dataclass
class Data:
    """The board's 'data': its latest readings."""

    soil_moisture: int  # %
    battery: int        # %
    air_humidity: int   # %
    temperature: int    # °C


def code_name(code: int) -> str:
    return f"{code >> 5}.{code & 0x1F:02d}"


def _option(delta: int, value: bytes) -> bytes:
    def nibble(n: int) -> tuple[int, bytes]:
        if n < 13:
            return n, b""
        if n < 269:
            return 13, bytes([n - 13])
        return 14, struct.pack(">H", n - 269)

    delta_nibble, delta_ext = nibble(delta)
    length_nibble, length_ext = nibble(len(value))
    return bytes([(delta_nibble << 4) | length_nibble]) + delta_ext + length_ext + value


def _uint(value: int) -> bytes:
    """An unsigned option value: the fewest bytes, none for 0."""
    return value.to_bytes((value.bit_length() + 7) // 8, "big") if value else b""


def encode(msg_type: int, code: int, message_id: int, token: bytes, path: str = "",
           payload: bytes = b"", query: tuple[str, ...] = (), observe: int | None = None) -> bytes:
    data = bytes([0x40 | (msg_type << 4) | len(token), code]) + struct.pack(">H", message_id) + token
    last = 0
    if observe is not None:
        data += _option(OPTION_OBSERVE, _uint(observe))
        last = OPTION_OBSERVE
    for segment in [s for s in path.split("/") if s]:
        data += _option(OPTION_URI_PATH - last, segment.encode())
        last = OPTION_URI_PATH
    for item in query:
        data += _option(OPTION_URI_QUERY - last, item.encode())
        last = OPTION_URI_QUERY
    if payload:
        data += b"\xff" + payload
    return data


@dataclass
class Message:
    type: int
    code: int
    message_id: int
    token: bytes
    path: str
    payload: bytes
    observe: int | None = None  # its Observe option, if it has one


def decode(data: bytes) -> Message:
    """Parses a CoAP message. Raises CoapError if it's malformed."""
    if len(data) < 4 or (data[0] >> 6) != 1:
        raise CoapError("not a CoAP message")
    msg_type = (data[0] >> 4) & 0x3
    token_length = data[0] & 0xF
    code = data[1]
    message_id, = struct.unpack_from(">H", data, 2)
    if token_length > 8 or len(data) < 4 + token_length:
        raise CoapError("bad token")
    token = data[4:4 + token_length]
    i = 4 + token_length
    number = 0
    segments = []
    observe = None
    payload = b""
    while i < len(data):
        if data[i] == 0xFF:
            payload = data[i + 1:]
            break
        delta, length = data[i] >> 4, data[i] & 0xF
        i += 1
        values = []
        for n in (delta, length):
            if n == 13:
                values.append(data[i] + 13)
                i += 1
            elif n == 14:
                values.append(struct.unpack_from(">H", data, i)[0] + 269)
                i += 2
            elif n == 15:
                raise CoapError("bad option")
            else:
                values.append(n)
        number += values[0]
        value = data[i:i + values[1]]
        i += values[1]
        if number == OPTION_URI_PATH:
            segments.append(value.decode(errors="replace"))
        elif number == OPTION_OBSERVE:
            observe = int.from_bytes(value, "big")
    return Message(msg_type, code, message_id, token, "/".join(segments), payload, observe)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        self.queue.put_nowait(exc)


async def request(host: str, path: str, method: int = GET, payload: bytes = b"", *, port: int = COAP_PORT,
                  query: tuple[str, ...] = (), ack_timeout: float = ACK_TIMEOUT_S,
                  max_retransmit: int = MAX_RETRANSMIT) -> tuple[int, bytes]:
    """Sends a confirmable request and returns the answer's (code, payload).

    Raises CoapError on a reset, TimeoutError without an answer, OSError when it can't be sent.
    """
    loop = asyncio.get_running_loop()
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    transport, protocol = await loop.create_datagram_endpoint(_Protocol, remote_addr=(host, port), family=family)
    message_id = random.randrange(0x10000)
    token = os.urandom(4)
    data = encode(TYPE_CON, method, message_id, token, path, payload, query)
    deadline = loop.time() + ack_timeout * (2 ** (max_retransmit + 1) - 1)
    acknowledged = False
    try:
        wait = ack_timeout
        transport.sendto(data)
        retransmits = 0
        next_send = loop.time() + wait
        while True:
            now = loop.time()
            if now >= deadline:
                raise TimeoutError(f"no answer from {host} for '{path}'")
            limit = deadline if acknowledged else min(deadline, next_send)
            try:
                item = await asyncio.wait_for(protocol.queue.get(), max(0.0, limit - now))
            except TimeoutError:  # asyncio.TimeoutError is TimeoutError since Python 3.11
                if not acknowledged and retransmits < max_retransmit:
                    retransmits += 1
                    wait *= 2
                    transport.sendto(data)
                    next_send = loop.time() + wait
                continue
            if isinstance(item, Exception):
                raise CoapError(f"{host}: {item}")
            try:
                message = decode(item)
            except CoapError:
                continue
            if message.type == TYPE_RST and message.message_id == message_id:
                raise CoapError(f"{host} reset the request for '{path}'")
            if message.type == TYPE_ACK and message.message_id == message_id:
                if message.code == 0:
                    acknowledged = True  # an empty ACK: the answer comes separately
                    continue
                if message.token == token:
                    return message.code, message.payload
            if message.token == token and message.type in (TYPE_CON, TYPE_NON) and message.code >= 0x40:
                if message.type == TYPE_CON:
                    transport.sendto(encode(TYPE_ACK, 0, message.message_id, b""))
                return message.code, message.payload
    finally:
        transport.close()


async def get(host: str, path: str, **kwargs) -> bytes:
    """The payload of a GET that answers 2.05 Content. Raises CoapError otherwise."""
    code, payload = await request(host, path, GET, **kwargs)
    if code != CONTENT:
        raise CoapError(f"'{path}' answered {code_name(code)}")
    return payload


async def put(host: str, path: str, payload: bytes, **kwargs) -> bytes:
    """The payload of a PUT's answer (2.04 Changed or 2.05 Content). Raises CoapError otherwise."""
    code, answer = await request(host, path, PUT, payload, **kwargs)
    if code not in (CHANGED, CONTENT):
        raise CoapError(f"'{path}' answered {code_name(code)}")
    return answer


def parse_data(payload: bytes) -> Data:
    """'data': soil, battery and air humidity (%, unsigned), then the temperature (°C, signed)."""
    if len(payload) < 4:
        raise CoapError(f"unexpected 'data' payload: {payload!r}")
    temperature = payload[3] - 256 if payload[3] > 127 else payload[3]
    return Data(payload[0], payload[1], payload[2], temperature)


def parse_info(payload: bytes) -> Info:
    """'info': "<fw>,<hw>,<device id>" (with its NUL). Older firmware has fewer parts."""
    parts = [p.strip() for p in payload.rstrip(b"\0").decode("ascii", errors="replace").split(",")]
    if not parts[0]:
        raise CoapError(f"unexpected 'info' payload: {payload!r}")
    parts += [""] * (3 - len(parts))
    return Info(parts[0], parts[1], parts[2].lower())


# ---------------------------------------------------------------- 'diag': the board's health

@dataclass
class Diagnostics:
    """The 'diag' payload (application/include/diag.h in ha-coap-server): "key=value,..."."""

    boots: int                   # boots since its flash was erased
    uptime_s: int
    reset: str                   # why it last restarted: power, pin, soft, watchdog, lockup, crash, other
    fault: str                   # after a crash: "<reason>@<address>", else ""
    role: str                    # disabled, detached, child, router, leader
    rloc16: str                  # "0x5c01", "" when not attached
    parent: str                  # its parent router's, "0x5c00"
    rssi: int | None             # its parent's average RSSI (dBm)
    link_quality: int | None     # 0 to 3
    soil_mv: int | None          # the soil sensor's output (mV)
    power: str                   # battery, usb, charging, charged ("" before this field)
    button_s: int | None         # its pump's run time started with its button since boot (s)
    pump_ms: int | None          # its pump's run time since boot, whatever started it (ms)
    observers: int               # how many can observe 'hist' at once (1 before this field)
    pump_s: int | None = None    # its pump's run time setting (s), 1 to 9 (None before this field)


def parse_diag(payload: bytes) -> Diagnostics:
    fields = {}
    for item in payload.rstrip(b"\0").decode("ascii", errors="replace").split(","):
        key, equals, value = item.partition("=")
        if equals:
            fields[key.strip()] = value.strip()
    if "boots" not in fields or "reset" not in fields:
        raise CoapError(f"unexpected 'diag' payload: {payload[:60]!r}")

    def number(key: str) -> int | None:
        try:
            return int(fields[key])
        except (KeyError, ValueError):
            return None

    def hex16(key: str) -> str:
        return f"0x{fields[key]}" if fields.get(key) else ""

    soil_mv = number("soil_mv")
    pump_s = number("pump_s")
    return Diagnostics(number("boots") or 0, number("up") or 0, fields["reset"], fields.get("fault", ""),
                       fields.get("role", ""), hex16("rloc"), hex16("parent"), number("rssi"), number("lq"),
                       soil_mv if soil_mv is not None and soil_mv >= 0 else None, fields.get("power", ""),
                       number("button_s"), number("pump_ms"), number("observers") or 1,
                       pump_s if pump_s is not None and 1 <= pump_s <= 9 else None)


# ---------------------------------------------------------------- 'hist': the samples a board keeps

HISTORY_HEADER = struct.Struct("<BBHIIII")  # version, count, entry size, boot, uptime, first, next
HISTORY_ENTRY = struct.Struct("<IBBBbB")    # uptime, soil, battery, air humidity, temperature, power
POWER_NAMES = ("battery", "usb", "charging", "charged")


@dataclass
class Sample:
    seq: int          # its number since the board's boot
    uptime_s: int     # when, in seconds since its boot
    soil_moisture: int
    battery: int
    air_humidity: int
    temperature: int
    power: str


@dataclass
class History:
    boot: int         # the board's boot count: another one means a restart (the numbers start again)
    uptime_s: int     # its uptime when it answered
    first: int        # the number of the first sample here
    next: int         # the number of its next sample: more to read when it's after the last one here
    samples: list[Sample]

    @property
    def read_up_to(self) -> int:
        """The number after the last sample here: where to read from next."""
        return self.first + len(self.samples)

    def time_of(self, sample: Sample, received_at: float) -> float:
        """A sample's time, from the board's uptime when it answered."""
        return received_at - (self.uptime_s - sample.uptime_s)


def parse_history(payload: bytes) -> History:
    if len(payload) < HISTORY_HEADER.size:
        raise CoapError(f"unexpected 'hist' payload: {payload[:30]!r}")
    version, count, size, boot, uptime, first, next_seq = HISTORY_HEADER.unpack_from(payload)
    if version < 1 or size < HISTORY_ENTRY.size or len(payload) < HISTORY_HEADER.size + count * size:
        raise CoapError(f"unexpected 'hist' payload: {payload[:30]!r}")
    samples = []
    for i in range(count):
        uptime_s, soil, battery, air, temperature, power = HISTORY_ENTRY.unpack_from(payload, HISTORY_HEADER.size + i * size)
        samples.append(Sample(first + i, uptime_s, soil, battery, air, temperature,
                              POWER_NAMES[power] if power < len(POWER_NAMES) else ""))
    return History(boot, uptime, first, next_seq, samples)


class _ObserverProtocol(asyncio.DatagramProtocol):
    def __init__(self, observer: Observer) -> None:
        self.observer = observer
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.observer._received(self.transport, data, addr)

    def error_received(self, exc: Exception) -> None:
        pass  # a board out of reach: its registration times out


class Observer:
    """Observes 'hist' on the boards (RFC 7641), from one UDP socket per address family: a
    registration is a confirmable GET with Observe 0, its answer has the samples asked for, and
    the board then sends its new samples to on_notification(host, payload) when one changed
    enough. Registrations last 2 hours on the board: register again before (each reading does)."""

    def __init__(self, on_notification) -> None:
        self.on_notification = on_notification
        self._endpoints: dict = {}      # address family: transport
        self._tokens: dict = {}         # host: its token (the same at every registration)
        self._hosts: dict = {}          # token: host
        self._pending: dict = {}        # token: (message ID, future of the answer)
        self._acknowledged: set = set()  # message IDs with an empty ACK: the answer comes on its own

    async def _endpoint(self, family):
        if family not in self._endpoints:
            loop = asyncio.get_running_loop()
            local = ("::", 0) if family == socket.AF_INET6 else ("0.0.0.0", 0)
            transport, _ = await loop.create_datagram_endpoint(lambda: _ObserverProtocol(self), local_addr=local,
                                                               family=family)
            self._endpoints[family] = transport
        return self._endpoints[family]

    def close(self) -> None:
        for transport in self._endpoints.values():
            transport.close()
        self._endpoints = {}

    def forget(self, host: str) -> None:
        """No more notifications from this host are passed on (its entry was unloaded)."""
        token = self._tokens.pop(host, None)
        self._hosts.pop(token, None)

    async def register(self, host: str, path: str = "hist", query: tuple[str, ...] = (), *, port: int = COAP_PORT,
                       ack_timeout: float = ACK_TIMEOUT_S, max_retransmit: int = MAX_RETRANSMIT):
        """Registers (GET with Observe 0) and returns (code, payload, observed) of the answer:
        'observed' when the board took the registration. Raises TimeoutError without an answer,
        CoapError on a reset."""
        loop = asyncio.get_running_loop()
        transport = await self._endpoint(socket.AF_INET6 if ":" in host else socket.AF_INET)
        token = self._tokens.setdefault(host, os.urandom(8))
        self._hosts[token] = host
        message_id = random.randrange(0x10000)
        data = encode(TYPE_CON, GET, message_id, token, path, query=query, observe=0)
        future = loop.create_future()
        self._pending[token] = (message_id, future)
        deadline = loop.time() + ack_timeout * (2 ** (max_retransmit + 1) - 1)
        wait = ack_timeout
        try:
            transport.sendto(data, (host, port))
            for _ in range(max_retransmit + 1):
                try:
                    return await asyncio.wait_for(asyncio.shield(future), max(0.0, min(wait, deadline - loop.time())))
                except TimeoutError:
                    if loop.time() >= deadline:
                        break
                    if message_id not in self._acknowledged:
                        transport.sendto(data, (host, port))
                    wait *= 2
            remaining = deadline - loop.time()
            if remaining > 0:
                try:
                    return await asyncio.wait_for(asyncio.shield(future), remaining)
                except TimeoutError:
                    pass
            raise TimeoutError(f"no answer from {host} for '{path}'")
        finally:
            if self._pending.get(token, (None, None))[1] is future:
                del self._pending[token]
            self._acknowledged.discard(message_id)
            if not future.done():
                future.cancel()

    def _received(self, transport, data: bytes, addr) -> None:
        try:
            message = decode(data)
        except CoapError:
            return
        if message.type == TYPE_CON:
            transport.sendto(encode(TYPE_ACK, 0, message.message_id, b""), addr)  # whatever it is
        if message.type in (TYPE_ACK, TYPE_RST):
            for token, (message_id, future) in list(self._pending.items()):
                if message_id != message.message_id or future.done():
                    continue
                if message.type == TYPE_RST:
                    future.set_exception(CoapError(f"{addr[0]} reset the request"))
                elif message.code == 0:
                    self._acknowledged.add(message_id)  # an empty ACK: the answer comes separately
                elif message.token == token:
                    future.set_result((message.code, message.payload, message.observe is not None))
                return
        if message.code < 0x40:
            return
        pending = self._pending.get(message.token)
        if pending is not None and not pending[1].done():
            pending[1].set_result((message.code, message.payload, message.observe is not None))
        elif message.observe is not None and message.code == CONTENT and message.token in self._hosts:
            self.on_notification(self._hosts[message.token], message.payload)
