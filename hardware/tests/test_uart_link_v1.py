import json
from pathlib import Path
import uuid

from onenet_wire import decode_service_command
from uart_link import UartLink, compute_mcu_payload_sha256
from uart_protocol import (
    MESSAGE_TYPE,
    compute_command_digest,
    decode_frame,
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
