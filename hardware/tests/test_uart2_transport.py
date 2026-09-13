"""Actual pyserial loopback, never a device path or GPIO."""
import json
from pathlib import Path

import serial
import pytest

import uart2_protocol as uart


def test_loopback_reads_every_reply_once_with_bounded_polling():
    from uart2_transport import NativeUartTransport
    port = serial.serial_for_url("loop://", baudrate=115200, timeout=0, write_timeout=0.1)
    try:
        transport = NativeUartTransport(port)
        reply = uart.encode_frame("BOOT_PROBE_REPLY", 1,
            uart.encode_payload("BOOT_PROBE_REPLY", {"probeId": 1, "mcuBootId": 0}))
        port.write(reply * 30)
        received = transport.poll(0)
        assert 0 < len(received) < 30
        received += transport.poll(1)
        assert received == [reply] * 30
        assert transport.poll(2) == []
        # Looping back our request is wrong direction, not an MCU answer.
        request = uart.encode_frame("BOOT_PROBE", 2, uart.encode_payload("BOOT_PROBE", {"probeId": 2}))
        assert transport.write(request) == len(request)
        assert transport.poll(3) == []
        assert transport.diagnostics
    finally:
        port.close()


def test_transport_never_flushes_or_retries_a_short_write():
    from uart2_transport import NativeUartTransport
    class Boundary:
        timeout, write_timeout, is_open = 0, 0.1, True
        writes = []
        def write(self, frame):
            self.writes.append(frame)
            return 2
        def flush(self):
            pytest.fail("flush is not part of one bounded dispatch")
    boundary = Boundary()
    transport = NativeUartTransport(boundary)
    frame = uart.encode_frame("BOOT_PROBE", 1, uart.encode_payload("BOOT_PROBE", {"probeId": 1}))
    assert transport.write(frame) == 2 and boundary.writes == [frame]
    boundary.write_timeout = None
    with pytest.raises(ValueError, match="bounded"):
        transport.write(frame)
    assert boundary.writes == [frame]


@pytest.mark.parametrize("name", ["DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT", "CLEAN_LOCK_POWER_CHANGED"])
def test_actuator_loopback_rejects_bad_identity_and_preserves_following_reports(name):
    from uart2_transport import NativeUartTransport
    fixture = Path(__file__).resolve().parents[2] / "contracts/examples/uart/golden-vectors.json"
    vectors = json.loads(fixture.read_text(encoding="utf-8"))["vectors"]
    vector = next(item for item in vectors if item["messageName"] == name)
    good = bytes.fromhex(vector["frameHex"])
    payload = bytearray(bytes.fromhex(vector["payloadHex"]))
    field = next(field for field in uart.MESSAGE_SPECS[name]["fields"] if field["name"] == "mcuCommandUid")
    payload[field["offset"]:field["offset"] + 16] = bytes(16)
    bad = uart.encode_frame(name, 900, bytes(payload))
    port = serial.serial_for_url("loop://", baudrate=115200, timeout=0, write_timeout=0.1)
    try:
        transport = NativeUartTransport(port)
        port.write(bad + good + good)
        assert transport.poll(0) == [good, good]
        assert list(transport.diagnostics) == ["SEMANTIC_REJECTED:invalid session payload"]
        assert transport.poll(1) == []
        # The transport validates and forwards; it must not ACK/save/replay an action.
        assert port.in_waiting == 0
    finally:
        port.close()
