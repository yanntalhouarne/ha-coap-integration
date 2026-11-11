"""A small CoAP client (RFC 7252) for the boards' resources.

Only what the boards need: GET and PUT, confirmable requests with retransmission, piggybacked
or separate responses. The same client as the ha-coap fleet manager's (manager/hacoap_manager/
coap.py in the ha-coap-server repository). Timeouts are long because a sleepy board only gets a
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
OPTION_URI_PATH, OPTION_URI_QUERY = 11, 15

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


def encode(msg_type: int, code: int, message_id: int, token: bytes, path: str = "",
           payload: bytes = b"", query: tuple[str, ...] = ()) -> bytes:
    data = bytes([0x40 | (msg_type << 4) | len(token), code]) + struct.pack(">H", message_id) + token
    last = 0
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
    return Message(msg_type, code, message_id, token, "/".join(segments), payload)


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
