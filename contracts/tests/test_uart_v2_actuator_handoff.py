"""Actuator custody is separate from measurement receipts and business state."""
from contracts.tests.test_uart_v2_process_measurement import codec
from contracts.tests.test_uart_v2_process_measurement import c_guard
from contractlib import load_uart_registry, encode_uart_payload, decode_uart_payload, uart_actuator_event_digest_preimage
import pytest
import hashlib
from contracts.tests.test_uart_v2_actuator_events import actuator_values


def test_query_echo_names_one_retained_actuator_event_without_claiming_business_completion(codec):
    request = dict(queryId=23, targetMcuBootId=42, afterMcuEventSequence=0)
    raw = codec.encode_payload("QUERY_ACTUATOR_EVENT", request)
    assert len(raw) == 20
    reply = dict(request, currentMcuBootId=42, status="HELD", mcuEventSequence=7,
                 eventMessageType="CLEAN_LOCK_POWER_CHANGED", eventDigestSha256="22" * 32)
    encoded = codec.encode_payload("ACTUATOR_EVENT_QUERY_REPLY", reply)
    assert encoded[:20] == raw and len(encoded) == 66
    assert codec.decode_payload("ACTUATOR_EVENT_QUERY_REPLY", encoded) == reply


def test_held_reply_cannot_refer_to_an_event_at_or_before_the_query_cursor(codec):
    with pytest.raises(codec.ProtocolError):
        codec.encode_payload("ACTUATOR_EVENT_QUERY_REPLY", dict(queryId=23, targetMcuBootId=42,
            afterMcuEventSequence=7, currentMcuBootId=42, status="HELD", mcuEventSequence=7,
            eventMessageType="CLEAN_LOCK_POWER_CHANGED", eventDigestSha256="22" * 32))


def test_actuator_receipt_hashes_original_type_and_every_body_byte_in_its_own_domain(codec):
    name = "CLEAN_LOCK_POWER_CHANGED"
    raw = codec.encode_payload(name, actuator_values(name))
    expected = hashlib.sha256(b"ECOBIN:UART:ACTUATOR-EVENT:v2\0" + bytes([53]) + len(raw).to_bytes(2, "big") + raw).hexdigest()
    assert codec.compute_actuator_event_digest(name, raw) == expected
    receipt = dict(mcuBootId=42, mcuEventSequence=7, eventMessageType=name, eventDigestSha256=expected)
    encoded = codec.encode_payload("ACTUATOR_EVENT_SAVED", receipt)
    assert len(encoded) == 45 and codec.decode_payload("ACTUATOR_EVENT_SAVED", encoded) == receipt
    with pytest.raises(codec.ProtocolError):
        codec.encode_payload("PROCESS_EVENT_SAVED", receipt)


@pytest.mark.parametrize("changes", [dict(currentMcuBootId=0), dict(mcuEventSequence=0),
    dict(afterMcuEventSequence=7), dict(eventMessageType="NONE"), dict(status="NOT_FOUND")])
def test_contradictory_query_payload_is_rejected_by_reference_python_and_c(codec, c_guard, changes):
    value = dict(queryId=23, targetMcuBootId=42, afterMcuEventSequence=0, currentMcuBootId=42,
                 status="HELD", mcuEventSequence=7, eventMessageType="CLEAN_LOCK_POWER_CHANGED", eventDigestSha256="22" * 32)
    name = "ACTUATOR_EVENT_QUERY_REPLY"
    bad = encode_uart_payload(load_uart_registry(), name, value | changes, validate_semantics=False)
    with pytest.raises(ValueError):
        encode_uart_payload(load_uart_registry(), name, value | changes)
    with pytest.raises(ValueError):
        codec.decode_payload(name, bad)
    frame = codec.encode_frame(name, 1, bad)
    assert c_guard(frame, len(frame)) != 0


@pytest.mark.parametrize("name", ["CLEAN_LOCK_POWER_CHANGED", "DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT"])
def test_all_actuator_digests_match_reference_and_reject_process_inputs(codec, name):
    raw = codec.encode_payload(name, actuator_values(name))
    preimage = uart_actuator_event_digest_preimage(load_uart_registry(), name, raw)
    assert codec.compute_actuator_event_digest(name, raw) == hashlib.sha256(preimage).hexdigest()
    with pytest.raises(ValueError):
        codec.compute_process_event_digest(name, raw)


def test_saved_receipt_cannot_use_the_query_only_none_reference_type(codec, c_guard):
    values = dict(mcuBootId=42, mcuEventSequence=7, eventMessageType=0, eventDigestSha256="22" * 32)
    raw = (42).to_bytes(8, "big") + (7).to_bytes(4, "big") + b"\0" + bytes.fromhex("22" * 32)
    with pytest.raises(ValueError):
        decode_uart_payload(load_uart_registry(), "ACTUATOR_EVENT_SAVED", raw)
    with pytest.raises(ValueError):
        codec.encode_payload("ACTUATOR_EVENT_SAVED", values)
