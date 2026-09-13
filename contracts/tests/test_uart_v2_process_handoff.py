"""A process lookup names the original command, work, step and configuration."""
from contracts.tests.test_uart_v2_process_measurement import codec, process_values
from contractlib import load_uart_registry, encode_uart_payload
import pytest
import hashlib


def process_scope():
    value = process_values()
    return dict(mcuCommandUid=value["mcuCommandUid"], commandDigestSha256="11" * 32,
                targetMcuBootId=42, commandSequence=1, workUid=value["sessionUid"],
                workType="DELIVERY_SESSION", portNo=1,
                eventMessageType="WORK_PREOPEN_WEIGHT_READY", stepSequence=1, configVersion=7)


def test_query_echo_identifies_the_original_measurement_not_the_latest_step(codec):
    values = dict(queryId=23, **process_scope())
    payload = encode_uart_payload(load_uart_registry(), "QUERY_PROCESS_EVENT", values)
    assert len(payload) == 97
    assert codec.decode_payload("QUERY_PROCESS_EVENT", payload) == values
    reply = dict(values, currentMcuBootId=42, status="HELD", mcuEventSequence=3,
                 eventDigestSha256="22" * 32)
    encoded = codec.encode_payload("PROCESS_EVENT_QUERY_REPLY", reply)
    assert encoded[:len(payload)] == payload
    assert codec.decode_payload("PROCESS_EVENT_QUERY_REPLY", encoded) == reply


@pytest.mark.parametrize("changes", [dict(workType="CLEAN_OPERATION"), dict(stepSequence=0),
    dict(currentMcuBootId=0), dict(status="NOT_FOUND"), dict(mcuEventSequence=0)])
def test_query_cannot_claim_a_held_measurement_with_inconsistent_scope_or_reference(codec, changes):
    values = dict(queryId=23, **process_scope(), currentMcuBootId=42,
                  status="HELD", mcuEventSequence=3, eventDigestSha256="22" * 32)
    values.update(changes)
    with pytest.raises(codec.ProtocolError):
        codec.encode_payload("PROCESS_EVENT_QUERY_REPLY", values)


def test_saved_receipt_covers_the_exact_original_message_and_all_payload_bytes(codec):
    from contractlib import compute_uart_process_event_digest
    name = "WORK_PREOPEN_WEIGHT_READY"
    payload = codec.encode_payload(name, process_values())
    expected = hashlib.sha256(b"ECOBIN:UART:PROCESS-EVENT:v2\0" + bytes([48])
                              + len(payload).to_bytes(2, "big") + payload).hexdigest()
    assert codec.compute_process_event_digest(name, payload) == expected
    assert compute_uart_process_event_digest(load_uart_registry(), name, payload) == expected
    receipt = dict(mcuBootId=42, mcuEventSequence=3, eventMessageType=name, eventDigestSha256=expected)
    encoded = codec.encode_payload("PROCESS_EVENT_SAVED", receipt)
    assert len(encoded) == 45
    assert codec.decode_payload("PROCESS_EVENT_SAVED", encoded) == receipt
    reply = dict(receipt, currentMcuBootId=42, status="RELEASED")
    assert codec.encode_payload("PROCESS_EVENT_SAVED_REPLY", reply)[:45] == encoded
    reply["currentMcuBootId"] = 0
    with pytest.raises(codec.ProtocolError):
        codec.encode_payload("PROCESS_EVENT_SAVED_REPLY", reply)
