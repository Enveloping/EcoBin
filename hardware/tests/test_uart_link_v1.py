import json
from pathlib import Path
import uuid

import pytest

from onenet_wire import decode_service_command
from uart_link import UartError, UartLink, compute_mcu_payload_sha256
from uart_protocol import (
    MESSAGE_TYPE,
    ProtocolError,
    compute_command_digest,
    decode_frame,
    decode_payload,
    encode_frame,
    encode_payload,
)


class AutoAckSerial:
    def __init__(self, edge_boot_id, mcu_boot_id, ack_after_write=1):
        self.edge_boot_id = edge_boot_id
        self.mcu_boot_id = mcu_boot_id
        self.ack_after_write = ack_after_write
        self.is_open = True
        self.timeout = 0.5
        self.writes = []
        self._incoming = bytearray()
        self._mcu_tx_sequence = 0

    @property
    def in_waiting(self):
        return len(self._incoming)

    def write(self, data):
        self.writes.append(bytes(data))
        decoded = decode_frame(data, sender_role="EDGE")
        if len(self.writes) >= self.ack_after_write:
            self._mcu_tx_sequence += 1
            ack_payload = encode_payload("ACK", {
                "senderBootId": self.mcu_boot_id,
                "referencedSenderBootId": self.edge_boot_id,
                "referencedTxSequence": decoded["txSequence"],
                "referencedMessageType": decoded["messageType"],
                "disposition": "ACCEPTED",
            })
            self._incoming.extend(
                encode_frame("ACK", self._mcu_tx_sequence, ack_payload)
            )
        return len(data)

    def flush(self):
        pass

    def read(self, size):
        if not self._incoming:
            return b""
        chunk = bytes(self._incoming[:size])
        del self._incoming[:size]
        return chunk

    def close(self):
        self.is_open = False


class EventBeforeAckSerial(AutoAckSerial):
    def write(self, data):
        if not self.writes:
            result_payload = encode_payload("CONFIG_APPLY_RESULT", {
                "mcuBootId": self.mcu_boot_id,
                "mcuEventSequence": 1,
                "uptimeMs": 100,
                "mcuCommandUid": "30000000-0000-4000-8000-000000000001",
                "applicationUid": "40000000-0000-4000-8000-000000000001",
                "status": "APPLIED",
                "configVersion": 8,
                "contentSha256": "a" * 64,
                "mcuPayloadSha256": "b" * 64,
                "faultCode": "NONE",
            })
            self._mcu_tx_sequence += 1
            self._incoming.extend(
                encode_frame(
                    "CONFIG_APPLY_RESULT",
                    self._mcu_tx_sequence,
                    result_payload,
                )
            )
        return super().write(data)


class HandshakeSerial:
    def __init__(
        self,
        edge_boot_id,
        mcu_boot_id,
        mcu_capability,
        repeat_hello_before_ack=False,
    ):
        self.edge_boot_id = edge_boot_id
        self.mcu_boot_id = mcu_boot_id
        self.mcu_capability = mcu_capability
        self.repeat_hello_before_ack = repeat_hello_before_ack
        self.is_open = True
        self.timeout = 0.5
        self.writes = []
        self._incoming = bytearray()
        self._mcu_tx_sequence = 0

    def _queue_hello(self):
        self._mcu_tx_sequence += 1
        hello = encode_payload("HELLO", {
            "senderRole": "MCU",
            "senderBootId": self.mcu_boot_id,
            "supportedMajor": 1,
            "minimumMinor": 0,
            "maximumMinor": 0,
            "portCount": 1,
            "capabilityBitmap": self.mcu_capability,
            "maximumFrameLength": 256,
            "pendingCriticalEventCount": 0,
            "firmwareIdentity": "stm32f103rct6",
            "firmwareVersion": "1.0.0-hil.1",
        })
        self._incoming.extend(
            encode_frame("HELLO", self._mcu_tx_sequence, hello)
        )

    def _queue_hello_ack(self):
        self._mcu_tx_sequence += 1
        hello_ack = encode_payload("HELLO_ACK", {
            "responderBootId": self.mcu_boot_id,
            "referencedSenderBootId": self.edge_boot_id,
            "selectedMajor": 1,
            "selectedMinor": 0,
            "status": "ACCEPTED",
            "portCount": 1,
            "capabilityBitmap": self.mcu_capability,
            "maximumFrameLength": 256,
            "errorCode": "NONE",
        })
        self._incoming.extend(
            encode_frame("HELLO_ACK", self._mcu_tx_sequence, hello_ack)
        )

    @property
    def in_waiting(self):
        return len(self._incoming)

    def write(self, data):
        self.writes.append(bytes(data))
        decoded = decode_frame(data, sender_role="EDGE")
        if decoded["messageType"] == MESSAGE_TYPE["HELLO"]:
            self._queue_hello()
        elif decoded["messageType"] == MESSAGE_TYPE["HELLO_ACK"]:
            if self.repeat_hello_before_ack:
                self._queue_hello()
            self._queue_hello_ack()
        return len(data)

    def flush(self):
        pass

    def read(self, size):
        if not self._incoming:
            return b""
        chunk = bytes(self._incoming[:size])
        del self._incoming[:size]
        return chunk

    def close(self):
        self.is_open = False


def test_snapshot_end_rejects_nonzero_range_for_empty_pending_queue():
    payload = bytearray(encode_payload("STATE_SNAPSHOT_END", {
        "mcuBootId": 42,
        "mcuEventSequence": 3,
        "uptimeMs": 100,
        "snapshotUid": str(uuid.uuid4()),
        "partIndex": 3,
        "partCount": 3,
        "pendingCriticalEventCount": 0,
        "oldestPendingEventBootId": 0,
        "oldestPendingEventSequence": 0,
        "latestPendingEventBootId": 0,
        "latestPendingEventSequence": 0,
        "snapshotSha256": bytes(32),
    }))
    payload[63] = 1

    with pytest.raises(
        ProtocolError,
        match="empty pending-event queue requires zero range",
    ):
        decode_payload("STATE_SNAPSHOT_END", bytes(payload))


def query_state_payload():
    values = {
        "mcuCommandUid": "10000000-0000-4000-8000-000000000001",
        "commandDigestSha256": bytes(32),
        "snapshotUid": "20000000-0000-4000-8000-000000000001",
    }
    values["commandDigestSha256"] = bytes.fromhex(
        compute_command_digest("QUERY_STATE", values)
    )
    return encode_payload("QUERY_STATE", values)


def test_handshake_requires_registry_baseline_by_default():
    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._ser = HandshakeSerial(7, 42, 0x300)

    result = link.handshake()

    assert result["mcu_capability"] == 0x300


def test_hil_handshake_can_require_a_stricter_slice():
    link = UartLink(
        port="fake",
        edge_boot_id=7,
        port_count=1,
        required_capability_bitmap=0x301,
    )
    link._ser = HandshakeSerial(7, 42, 0x300)

    with pytest.raises(UartError, match="能力不兼容"):
        link.handshake()


def test_handshake_tolerates_repeated_mcu_hello_before_hello_ack():
    link = UartLink(
        port="fake",
        edge_boot_id=7,
        port_count=1,
        required_capability_bitmap=0x300,
    )
    link._ser = HandshakeSerial(
        7,
        42,
        0x300,
        repeat_hello_before_ack=True,
    )

    result = link.handshake()

    assert result["mcu_boot_id"] == 42
    assert result["mcu_capability"] == 0x300


def test_online_mcu_hello_can_reestablish_the_uart_session():
    link = UartLink(
        port="fake",
        edge_boot_id=7,
        port_count=1,
        required_capability_bitmap=0x300,
    )
    serial = HandshakeSerial(7, 43, 0x300)
    link._ser = serial
    serial._queue_hello()
    hello = link.read_mcu_event(timeout_ms=20)

    assert hello["message_name"] == "HELLO"
    assert link.mcu_session_ready is False
    blocked = link._send_and_wait_ack(
        "QUERY_STATE",
        query_state_payload(),
        ack_timeout_ms=5,
        max_retries=1,
    )
    assert blocked == {
        "acked": False,
        "error": "UART_NOT_READY",
        "fatal": False,
    }
    assert serial.writes == []

    result = link.renegotiate_from_mcu_hello(hello)

    assert result["mcu_boot_id"] == 43
    assert link.mcu_session_ready is True
    sent_types = [
        decode_frame(frame, sender_role="EDGE")["messageType"]
        for frame in serial.writes
    ]
    assert sent_types == [MESSAGE_TYPE["HELLO_ACK"]]


def test_ack_is_matched_against_boot_sequence_and_message_type():
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42)

    result = link._send_and_wait_ack(
        "QUERY_STATE", query_state_payload(), ack_timeout_ms=20
    )

    assert result["acked"] is True
    assert result["disposition"] == "ACCEPTED"
    assert len(link._ser.writes) == 1


def test_timeout_retry_reuses_byte_identical_frame():
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42, ack_after_write=2)

    result = link._send_and_wait_ack(
        "QUERY_STATE",
        query_state_payload(),
        ack_timeout_ms=5,
        max_retries=2,
    )

    assert result["acked"] is True
    assert len(link._ser.writes) == 2
    assert link._ser.writes[0] == link._ser.writes[1]
    decoded = decode_frame(link._ser.writes[0], sender_role="EDGE")
    assert decoded["messageType"] == MESSAGE_TYPE["QUERY_STATE"]


def test_event_received_before_ack_remains_available_to_event_consumer():
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = EventBeforeAckSerial(7, 42)

    result = link._send_and_wait_ack(
        "QUERY_STATE", query_state_payload(), ack_timeout_ms=20
    )
    event = link.read_mcu_event(timeout_ms=1)

    assert result["acked"] is True
    assert event["message_name"] == "CONFIG_APPLY_RESULT"
    assert event["payload"]["mcuEventSequence"] == 1


def test_received_frame_is_normalized_and_payload_decoded():
    link = UartLink(port="fake", edge_boot_id=7)
    serial = AutoAckSerial(7, 42)
    link._ser = serial
    result_payload = encode_payload("CONFIG_APPLY_RESULT", {
        "mcuBootId": 42,
        "mcuEventSequence": 1,
        "uptimeMs": 100,
        "mcuCommandUid": "30000000-0000-4000-8000-000000000001",
        "applicationUid": "40000000-0000-4000-8000-000000000001",
        "status": "APPLIED",
        "configVersion": 8,
        "contentSha256": "a" * 64,
        "mcuPayloadSha256": "b" * 64,
        "faultCode": "NONE",
    })
    serial._incoming.extend(encode_frame("CONFIG_APPLY_RESULT", 12, result_payload))

    frame = link.read_mcu_event(timeout_ms=20)

    assert frame["message_name"] == "CONFIG_APPLY_RESULT"
    assert frame["message_type"] == 20
    assert frame["tx_sequence"] == 12
    assert frame["payload"]["status"] == "APPLIED"


def test_apply_configuration_sends_registry_segments_in_order():
    example = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "examples"
        / "onenet-wire"
        / "apply-configuration.service-wire.json"
    )
    with example.open(encoding="utf-8") as source:
        body = json.load(source)["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    command["payload"]["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(
        command["payload"]
    )
    part_uids = [
        str(uuid.uuid4()) for _ in range(len(command["payload"]["ports"]) + 3)
    ]
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42)

    result = link.apply_configuration(command, part_uids)

    assert result["acked"] is True
    message_types = [
        decode_frame(frame, sender_role="EDGE")["messageType"]
        for frame in link._ser.writes
        if decode_frame(frame, sender_role="EDGE")["messageType"] != MESSAGE_TYPE["ACK"]
    ]
    assert message_types == [
        MESSAGE_TYPE["CONFIG_BEGIN"],
        MESSAGE_TYPE["CONFIG_DEVICE_BLOCK"],
        MESSAGE_TYPE["CONFIG_PORT_BLOCK"],
        MESSAGE_TYPE["CONFIG_PORT_BLOCK"],
        MESSAGE_TYPE["CONFIG_COMMIT"],
    ]


def test_send_command_adds_stable_identity_and_valid_digest():
    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42)
    command_uid = "50000000-0000-4000-8000-000000000001"

    result = link.send_command(
        "START_DELIVERY_SESSION",
        {
            "sessionUid": "51000000-0000-4000-8000-000000000001",
            "portNo": 1,
            "configVersion": 8,
            "configContentSha256": "a" * 64,
            "unitPriceTenThousandths": 10000,
            "continueDeliveryWaitMs": 30000,
            "negativeWeightThresholdGrams": 500,
            "startExecutionWindowMs": 45000,
            "deliveryAutoCloseMs": 120000,
        },
        mcu_command_uid=command_uid,
    )

    assert result["acked"] is True
    assert result["mcu_command_uid"] == command_uid
    frame = decode_frame(link._ser.writes[0], sender_role="EDGE")
    assert frame["messageType"] == MESSAGE_TYPE["START_DELIVERY_SESSION"]
    payload = decode_payload("START_DELIVERY_SESSION", frame["payload"])
    assert payload["mcuCommandUid"] == command_uid
    assert payload["commandDigestSha256"] == compute_command_digest(
        "START_DELIVERY_SESSION", payload
    )
