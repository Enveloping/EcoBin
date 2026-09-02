import json
from pathlib import Path
import time
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
        event_before_hello_ack=False,
    ):
        self.edge_boot_id = edge_boot_id
        self.mcu_boot_id = mcu_boot_id
        self.mcu_capability = mcu_capability
        self.repeat_hello_before_ack = repeat_hello_before_ack
        self.event_before_hello_ack = event_before_hello_ack
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

    def _queue_config_result(self):
        self._mcu_tx_sequence += 1
        payload = encode_payload("CONFIG_APPLY_RESULT", {
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
        self._incoming.extend(
            encode_frame(
                "CONFIG_APPLY_RESULT",
                self._mcu_tx_sequence,
                payload,
            )
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
            if self.event_before_hello_ack:
                self._queue_config_result()
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


def test_renegotiation_preserves_event_arriving_before_hello_ack():
    link = UartLink(
        port="fake",
        edge_boot_id=7,
        port_count=1,
        required_capability_bitmap=0x300,
    )
    serial = HandshakeSerial(
        7,
        42,
        0x300,
        event_before_hello_ack=True,
    )
    link._ser = serial
    serial._queue_hello()
    hello = link.read_mcu_event(timeout_ms=20)

    result = link.renegotiate_from_mcu_hello(hello)
    event = link.read_mcu_event(timeout_ms=1)

    assert result["mcu_boot_id"] == 42
    assert event["message_name"] == "CONFIG_APPLY_RESULT"
    assert event["payload"]["mcuEventSequence"] == 1


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
    assert sent_types == [
        MESSAGE_TYPE["HELLO"],
        MESSAGE_TYPE["HELLO_ACK"],
    ]


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


def test_dispatch_deadline_stops_retransmission_after_one_unknown_write(
    monkeypatch,
):
    clock = {"value": 0.0}
    monkeypatch.setattr(
        "uart_link.time.monotonic", lambda: clock["value"]
    )
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42, ack_after_write=2)

    def lose_first_ack(*, timeout_ms):
        del timeout_ms
        clock["value"] = 2.0
        return None

    link._read_serial_frame = lose_first_ack
    result = link._send_and_wait_ack(
        "QUERY_STATE",
        query_state_payload(),
        ack_timeout_ms=5,
        max_retries=3,
        dispatch_deadline_monotonic=1.0,
    )

    assert result["acked"] is False
    assert result["error"] == "TIMEOUT"
    assert result["uart_write_attempted"] is True
    assert len(link._ser.writes) == 1


def test_dispatch_deadline_after_gate_can_stop_the_first_write(monkeypatch):
    clock = {"value": 0.0}
    monkeypatch.setattr(
        "uart_link.time.monotonic", lambda: clock["value"]
    )
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42)

    result = link._send_and_wait_ack(
        "QUERY_STATE",
        query_state_payload(),
        dispatch_deadline_monotonic=1.0,
        dispatch_gate=lambda: clock.update(value=2.0),
    )

    assert result["acked"] is False
    assert result["error"] == "COMMAND_EXPIRED"
    assert result["uart_write_attempted"] is False
    assert link._ser.writes == []


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


def test_query_state_acks_but_does_not_mix_stale_snapshot_segments(
    monkeypatch,
):
    command_uid = uuid.UUID("10000000-0000-4000-8000-000000000001")
    current_snapshot_uid = uuid.UUID(
        "20000000-0000-4000-8000-000000000001"
    )
    stale_snapshot_uid = "30000000-0000-4000-8000-000000000001"
    generated_uids = iter((command_uid, current_snapshot_uid))
    monkeypatch.setattr(
        "uart_link._uuid.uuid4",
        lambda: next(generated_uids),
    )

    def segment(name, snapshot_uid):
        return {
            "message_name": name,
            "message_type": MESSAGE_TYPE[name],
            "tx_sequence": 10,
            "payload": {
                "snapshotUid": snapshot_uid,
                "mcuBootId": 42,
                "mcuEventSequence": 1,
            },
        }

    frames = [
        segment("STATE_SNAPSHOT_BEGIN", stale_snapshot_uid),
        segment("STATE_SNAPSHOT_PORT", stale_snapshot_uid),
        segment("STATE_SNAPSHOT_END", stale_snapshot_uid),
        segment("STATE_SNAPSHOT_BEGIN", str(current_snapshot_uid)),
        segment("STATE_SNAPSHOT_PORT", str(current_snapshot_uid)),
        segment("STATE_SNAPSHOT_END", str(current_snapshot_uid)),
    ]
    acknowledged = []
    stale_acks = []
    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._mcu_boot_id = 42
    monkeypatch.setattr(
        link,
        "_send_and_wait_ack",
        lambda message_name, payload: {"acked": True},
    )
    monkeypatch.setattr(
        link,
        "_read_frame",
        lambda timeout_ms: frames.pop(0) if frames else None,
    )
    monkeypatch.setattr(
        link,
        "send_ack",
        lambda boot_id, tx_sequence, message_type: stale_acks.append(
            (boot_id, tx_sequence, message_type)
        ),
    )

    result = link.query_state(
        on_segment=lambda frame: acknowledged.append(frame)
    )

    assert len(acknowledged) == 3
    assert len(stale_acks) == 3
    assert len(result) == 3
    assert {
        frame["payload"]["snapshotUid"] for frame in result
    } == {str(current_snapshot_uid)}


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


def test_expired_absolute_dispatch_deadline_prevents_first_uart_write():
    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._mcu_boot_id = 42
    serial = AutoAckSerial(7, 42)
    link._ser = serial
    gate_calls = []

    result = link.send_command_before_deadline(
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
        mcu_command_uid="50000000-0000-4000-8000-000000000001",
        dispatch_deadline_monotonic=time.monotonic() - 1,
        dispatch_gate=lambda: gate_calls.append("gate"),
    )

    assert result["acked"] is False
    assert result["error"] == "COMMAND_EXPIRED"
    assert gate_calls == []
    assert serial.writes == []


def test_dispatch_gate_runs_under_uart_lock_once_before_all_ack_retries():
    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._mcu_boot_id = 42
    serial = AutoAckSerial(7, 42, ack_after_write=2)
    link._ser = serial
    order = []
    original_write = serial.write

    def tracked_write(data):
        order.append("write")
        return original_write(data)

    def dispatch_gate():
        assert link._io_lock._is_owned()
        order.append("gate")

    serial.write = tracked_write
    result = link.send_command_before_deadline(
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
        mcu_command_uid="50000000-0000-4000-8000-000000000001",
        dispatch_deadline_monotonic=time.monotonic() + 1,
        dispatch_gate=dispatch_gate,
    )

    assert result["acked"] is True
    assert order == ["gate", "write", "write"]


def test_dispatch_gate_failure_propagates_without_uart_write():
    class DispatchRejected(RuntimeError):
        pass

    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._mcu_boot_id = 42
    serial = AutoAckSerial(7, 42)
    link._ser = serial
    gate_calls = []

    def reject_dispatch():
        gate_calls.append("gate")
        raise DispatchRejected("permanent action was not armed")

    with pytest.raises(DispatchRejected, match="was not armed"):
        link.send_command_before_deadline(
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
            mcu_command_uid="50000000-0000-4000-8000-000000000001",
            dispatch_deadline_monotonic=time.monotonic() + 1,
            dispatch_gate=reject_dispatch,
        )

    assert gate_calls == ["gate"]
    assert serial.writes == []


def test_uart_write_failure_after_dispatch_gate_propagates():
    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._mcu_boot_id = 42
    serial = AutoAckSerial(7, 42)
    link._ser = serial
    gate_calls = []

    def failing_write(data):
        serial.writes.append(bytes(data))
        raise IOError("UART driver write failed")

    serial.write = failing_write
    with pytest.raises(IOError, match="driver write failed"):
        link.send_command_before_deadline(
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
            mcu_command_uid="50000000-0000-4000-8000-000000000001",
            dispatch_deadline_monotonic=time.monotonic() + 1,
            dispatch_gate=lambda: gate_calls.append("gate"),
        )

    assert gate_calls == ["gate"]
    assert len(serial.writes) == 1


@pytest.mark.parametrize(
    ("serial_open", "session_ready", "expected_error"),
    [
        (False, True, "UART_CLOSED"),
        (True, False, "UART_NOT_READY"),
    ],
)
def test_uart_precheck_failure_does_not_call_dispatch_gate(
    serial_open,
    session_ready,
    expected_error,
):
    link = UartLink(port="fake", edge_boot_id=7, port_count=1)
    link._mcu_boot_id = 42 if session_ready else None
    serial = AutoAckSerial(7, 42)
    serial.is_open = serial_open
    link._ser = serial
    gate_calls = []

    result = link.send_command_before_deadline(
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
        mcu_command_uid="50000000-0000-4000-8000-000000000001",
        dispatch_deadline_monotonic=time.monotonic() + 1,
        dispatch_gate=lambda: gate_calls.append("gate"),
    )

    assert result["acked"] is False
    assert result["error"] == expected_error
    assert gate_calls == []
    assert serial.writes == []


def _last_edge_command_payload(link, message_name):
    frame = decode_frame(link._ser.writes[-1], sender_role="EDGE")
    assert frame["messageType"] == MESSAGE_TYPE[message_name]
    return decode_payload(message_name, frame["payload"])


def test_confirm_no_active_work_sends_config_identity():
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42)

    result = link.send_confirm_no_active_work(23, "a" * 64)
    payload = _last_edge_command_payload(link, "CONFIRM_NO_ACTIVE_WORK")

    assert result["acked"] is True
    assert uuid.UUID(result["mcu_command_uid"])
    assert payload["configVersion"] == 23
    assert payload["configContentSha256"] == "a" * 64


def test_start_delivery_session_sends_frozen_runtime_values():
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42)
    session_uid = str(uuid.uuid4())

    result = link.send_start_delivery_session(
        session_uid=session_uid,
        port_no=1,
        config_version=23,
        config_content_sha256="b" * 64,
        unit_price_ten_thousandths=4500,
        continue_delivery_wait_ms=30000,
        negative_weight_threshold_grams=500,
        start_execution_window_ms=45000,
        delivery_auto_close_ms=120000,
    )
    payload = _last_edge_command_payload(link, "START_DELIVERY_SESSION")

    assert result["acked"] is True
    assert uuid.UUID(result["mcu_command_uid"])
    assert payload["sessionUid"] == session_uid
    assert payload["configVersion"] == 23
    assert payload["continueDeliveryWaitMs"] == 30000
    assert payload["deliveryAutoCloseMs"] == 120000


def test_door_helpers_return_the_dispatched_command_uid():
    link = UartLink(port="fake", edge_boot_id=7)
    link._mcu_boot_id = 42
    link._ser = AutoAckSerial(7, 42)

    authorize = link.send_authorize_delivery_first_open(
        session_uid=str(uuid.uuid4()),
        port_no=1,
        preopen_measurement_uid=str(uuid.uuid4()),
        parent_start_command_uid=str(uuid.uuid4()),
        remaining_ms=45000,
    )
    safe_close = link.send_safe_close_all()

    assert uuid.UUID(authorize["mcu_command_uid"])
    assert uuid.UUID(safe_close["mcu_command_uid"])
