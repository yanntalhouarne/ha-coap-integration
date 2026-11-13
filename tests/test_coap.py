"""The CoAP client, against a fake board on this computer (real UDP: these tests turn on the
sockets the test kit turns off, socket_enabled)."""

import asyncio

import pytest

from custom_components.ha_coap import coap


def test_encode_and_decode():
    data = coap.encode(coap.TYPE_CON, coap.PUT, 0x1234, b"\x01\x02", "pumpdc", b"5", ("from=0",))
    message = coap.decode(data)
    assert (message.type, message.code, message.message_id, message.token, message.path, message.payload) == \
        (coap.TYPE_CON, coap.PUT, 0x1234, b"\x01\x02", "pumpdc", b"5")
    with pytest.raises(coap.CoapError):
        coap.decode(b"\x00\x01")


def test_parse_data_reads_the_temperature_signed():
    data = coap.parse_data(bytes([42, 87, 55, 253]))
    assert (data.soil_moisture, data.battery, data.air_humidity, data.temperature) == (42, 87, 55, -3)
    assert coap.parse_data(bytes([0, 100, 0, 25])).temperature == 25
    with pytest.raises(coap.CoapError):
        coap.parse_data(b"\x01\x02")


def test_parse_info():
    info = coap.parse_info(b"b75c209,2.0,1A2B3C4D\x00")
    assert (info.fw_version, info.hw_version, info.device_id) == ("b75c209", "2.0", "1a2b3c4d")  # the commit kept whole
    assert coap.parse_info(b"3e0592d,1.0").device_id == ""   # older firmware: no device ID
    with pytest.raises(coap.CoapError):
        coap.parse_info(b"\x00")


def test_parse_diag():
    diag = coap.parse_diag(b"boots=12,up=3600,reset=power,role=child,srp=registered,rloc=5c01,parent=5c00,rssi=-62,"
                           b"last_rssi=-60,lq=3,soil_mv=1696,power=charging,button_s=6,pump_ms=14020,pump_s=3,observers=3")
    assert (diag.boots, diag.rloc16, diag.rssi, diag.power, diag.pump_ms, diag.pump_s, diag.observers) == \
        (12, "0x5c01", -62, "charging", 14020, 3, 3)
    older = coap.parse_diag(b"boots=12,up=0,reset=crash,fault=3@0001a2b4,role=detached,srp=off,soil_mv=-1")
    assert (older.fault, older.soil_mv, older.pump_ms, older.pump_s, older.observers) == ("3@0001a2b4", None, None, None, 1)
    with pytest.raises(coap.CoapError):
        coap.parse_diag(b"role=child")


class FakeBoard(asyncio.DatagramProtocol):
    """Answers like a board: piggybacked, or an empty ACK then the answer ('separate'), or
    nothing for the first 'drop' requests."""

    def __init__(self, separate=False, drop=0):
        self.separate = separate
        self.drop = drop
        self.received = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        message = coap.decode(data)
        if message.type == coap.TYPE_ACK:
            return
        self.received.append(message)
        if self.drop:
            self.drop -= 1
            return
        answer = b"\x2a\x57\x37\x15" if message.path == "data" else b""
        if self.separate:
            self.transport.sendto(coap.encode(coap.TYPE_ACK, 0, message.message_id, b""), addr)
            self.transport.sendto(coap.encode(coap.TYPE_CON, coap.CONTENT, 0x4242, message.token, payload=answer), addr)
        else:
            self.transport.sendto(coap.encode(coap.TYPE_ACK, coap.CONTENT, message.message_id, message.token,
                                              payload=answer), addr)


async def start(board):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: board, local_addr=("127.0.0.1", 0))
    return transport, transport.get_extra_info("sockname")[1]


@pytest.mark.parametrize("separate", [False, True])
async def test_a_request_and_its_answer(separate, socket_enabled):
    board = FakeBoard(separate=separate)
    transport, port = await start(board)
    try:
        assert coap.parse_data(await coap.get("127.0.0.1", "data", port=port)).soil_moisture == 42
    finally:
        transport.close()


async def test_a_lost_request_is_sent_again(socket_enabled):
    board = FakeBoard(drop=1)
    transport, port = await start(board)
    try:
        await coap.get("127.0.0.1", "data", port=port, ack_timeout=0.05)
        assert len(board.received) == 2 and board.received[0].message_id == board.received[1].message_id
    finally:
        transport.close()


async def test_no_answer_times_out(socket_enabled):
    board = FakeBoard(drop=10)
    transport, port = await start(board)
    try:
        with pytest.raises(TimeoutError):
            await coap.get("127.0.0.1", "data", port=port, ack_timeout=0.02, max_retransmit=1)
        assert len(board.received) == 2
    finally:
        transport.close()
